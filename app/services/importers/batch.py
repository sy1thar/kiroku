import asyncio
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ExternalMapping, SyncCursor
from app.services.importers.anilist import import_batch_from_anilist
from app.services.importers.base import (
    clean_html_markup,
    upsert_character_from_external,
    upsert_media_from_external,
)
from app.services.translation import fetch_wikipedia_ru, translate_text


async def _translate_pair_if_needed(
    title_en: str,
    desc_en: str,
    locale: str,
    media_type: str = "",
) -> tuple[str, str]:
    """Concurrently translate a title (using Wikipedia RU when available) and description for RU locale."""
    if locale != "ru":
        return title_en, ""
    clean_desc = clean_html_markup(desc_en)[:1200]
    wiki_ru = await fetch_wikipedia_ru(title_en, media_type) if (title_en and media_type in ("series", "game", "vn")) else ""
    t_coro = asyncio.sleep(0, result=(wiki_ru, "community_verified")) if wiki_ru else (
        translate_text(title_en, "ru") if title_en else asyncio.sleep(0, result=("", "api_raw"))
    )
    d_coro = translate_text(clean_desc, "ru") if clean_desc else asyncio.sleep(0, result=("", "api_raw"))
    (tr_t, _), (tr_d, _) = await asyncio.gather(t_coro, d_coro)
    return (tr_t or title_en), tr_d


async def import_batch_from_vndb(db: Session, count: int = 10, locale: str = "ru") -> dict:
    """Batch import visual novels & characters from VNDB Kana v2 API."""
    settings = get_settings()
    cursor = db.scalar(
        select(SyncCursor).where(SyncCursor.provider == "vndb", SyncCursor.category == "vn")
    )
    if not cursor:
        cursor = SyncCursor(provider="vndb", category="vn", next_page=1, total_imported=0)
        db.add(cursor)
        db.flush()

    page_num = cursor.next_page
    async with httpx.AsyncClient(timeout=15.0) as client:
        vn_resp = await client.post(
            f"{settings.vndb_api_url}/vn",
            json={
                "fields": "id, title, alttitle, released, rating, votecount, description, image.url, tags.name",
                "sort": "votecount",
                "reverse": True,
                "page": page_num,
                "results": count,
            },
        )
        vn_resp.raise_for_status()
        payload = vn_resp.json()
        vns = payload.get("results") or []
        has_next = bool(payload.get("more"))

        vn_ids = [vn["id"] for vn in vns]
        existing_ids = set(
            db.scalars(
                select(ExternalMapping.external_id).where(
                    ExternalMapping.entity_type == "media",
                    ExternalMapping.provider == "vndb",
                    ExternalMapping.external_id.in_(vn_ids) if vn_ids else False,
                )
            ).all()
        ) if vn_ids else set()
        new_vns = [vn for vn in vns if vn["id"] not in existing_ids]

        sem = asyncio.Semaphore(8)

        async def _fetch_chars(vn_id: str) -> list[dict]:
            async with sem:
                cr = await client.post(
                    f"{settings.vndb_api_url}/character",
                    json={
                        "filters": ["vn", "=", ["id", "=", vn_id]],
                        "fields": "id, name, original, description, image.url, sex",
                        "results": 3,
                    },
                )
                return (cr.json().get("results") or []) if cr.status_code == 200 else []

        char_lists = await asyncio.gather(*[_fetch_chars(vn["id"]) for vn in new_vns])

    loc_sem = asyncio.Semaphore(14)

    async def _loc_item(title_s: str, desc_s: str) -> tuple[str, str]:
        async with loc_sem:
            return await _translate_pair_if_needed(title_s, desc_s, locale)

    vn_locs = await asyncio.gather(
        *[_loc_item(vn.get("title") or "Untitled VN", vn.get("description") or "") for vn in new_vns]
    )
    flat_chars = [(i, j, ch) for i, chars in enumerate(char_lists) for j, ch in enumerate(chars)]
    ch_locs = await asyncio.gather(
        *[_loc_item(ch.get("name") or "Unknown", ch.get("description") or "") for _, _, ch in flat_chars]
    )
    ch_loc_map = {(i, j): loc for (i, j, _), loc in zip(flat_chars, ch_locs)}

    imported: list[dict] = []
    for i, (vn, chars, (t_ru, d_ru)) in enumerate(zip(new_vns, char_lists, vn_locs)):
        vn_id = vn["id"]
        canonical = vn.get("title") or "Untitled VN"
        year = int(vn["released"][:4]) if vn.get("released") and vn["released"][:4].isdigit() else None
        rating = round(vn["rating"] / 10.0, 1) if vn.get("rating") else 0.0
        tags = [t["name"] for t in (vn.get("tags") or [])[:5] if t.get("name")]
        image_url = (vn.get("image") or {}).get("url") or ""

        media = await upsert_media_from_external(
            db,
            provider="vndb",
            external_id=vn_id,
            external_url=f"https://vndb.org/{vn_id}",
            media_type="vn",
            canonical_title=canonical,
            native_title=vn.get("alttitle") or "",
            release_year=year,
            rating=rating,
            popularity=vn.get("votecount") or 0,
            cover_url=image_url,
            unit_label="hr",
            genres=tags,
            platforms=["PC"],
            synopsis_en=vn.get("description") or "",
            title_ru=t_ru,
            synopsis_ru=d_ru,
            target_locale=locale,
        )

        char_count = 0
        for j, ch in enumerate(chars):
            sex_raw = ch.get("sex")
            gender = {"m": "male", "f": "female"}.get(sex_raw[0], "unknown") if sex_raw else "unknown"
            c_name_ru, c_bio_ru = ch_loc_map.get((i, j), ("", ""))
            await upsert_character_from_external(
                db,
                provider="vndb",
                external_id=ch["id"],
                external_url=f"https://vndb.org/{ch['id']}",
                canonical_name=ch.get("name") or "Unknown",
                native_name=ch.get("original") or "",
                gender=gender,
                image_url=(ch.get("image") or {}).get("url") or "",
                bio_en=ch.get("description") or "",
                name_ru=c_name_ru,
                bio_ru=c_bio_ru,
                translate_bio=False,
                target_locale=locale,
                media_id=media.id,
                role="main" if char_count == 0 else "supporting",
            )
            char_count += 1

        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": (t_ru or canonical) if locale == "ru" else canonical,
                "media_type": "vn",
                "characters_linked": char_count,
            }
        )

    cursor.next_page = page_num + 1 if has_next else page_num
    cursor.total_imported += len(imported)
    db.commit()
    return {
        "category": "vn",
        "page_imported": page_num,
        "next_page": cursor.next_page,
        "has_next_page": has_next,
        "total_imported": cursor.total_imported,
        "imported_count": len(imported),
        "items": imported,
    }


async def import_batch_from_games(db: Session, count: int = 10, locale: str = "ru") -> dict:
    """Batch import top video games via SteamSpy + Steam Store public API (zero keys needed)."""
    cursor = db.scalar(
        select(SyncCursor).where(SyncCursor.provider == "steam", SyncCursor.category == "game")
    )
    if not cursor:
        cursor = SyncCursor(provider="steam", category="game", next_page=1, total_imported=0)
        db.add(cursor)
        db.flush()

    offset_idx = cursor.next_page - 1
    steam_page = offset_idx // 10
    steam_lang = "russian" if locale == "ru" else "english"
    async with httpx.AsyncClient(timeout=15.0) as client:
        sp_resp = await client.get(
            "https://steamspy.com/api.php",
            params={"request": "top100forever" if steam_page == 0 else "all", "page": steam_page},
        )
        if sp_resp.status_code == 404:
            return {
                "category": "game",
                "page_imported": cursor.next_page,
                "next_page": cursor.next_page,
                "has_next_page": False,
                "total_imported": cursor.total_imported,
                "imported_count": 0,
                "items": [],
            }
        sp_resp.raise_for_status()
        all_games = list((sp_resp.json() or {}).values())

        start = (offset_idx % 10) * count
        slice_games = all_games[start : start + count]

        if not slice_games:
            if steam_page == 0 and all_games:
                cursor.next_page = 11
                db.commit()
                return {
                    "category": "game",
                    "page_imported": offset_idx + 1,
                    "next_page": cursor.next_page,
                    "has_next_page": True,
                    "total_imported": cursor.total_imported,
                    "imported_count": 0,
                    "items": [],
                }
            return {
                "category": "game",
                "page_imported": cursor.next_page,
                "next_page": cursor.next_page,
                "has_next_page": False,
                "total_imported": cursor.total_imported,
                "imported_count": 0,
                "items": [],
            }

        appids = [str(g["appid"]) for g in slice_games if g.get("appid")]
        existing_ids = set(
            db.scalars(
                select(ExternalMapping.external_id).where(
                    ExternalMapping.entity_type == "media",
                    ExternalMapping.provider == "steam",
                    ExternalMapping.external_id.in_(appids) if appids else False,
                )
            ).all()
        ) if appids else set()
        new_games = [g for g in slice_games if g.get("appid") and str(g["appid"]) not in existing_ids]

        sem = asyncio.Semaphore(8)

        async def _fetch_steam_detail(appid: int) -> dict:
            async with sem:
                try:
                    r = await client.get(
                        "https://store.steampowered.com/api/appdetails",
                        params={"appids": str(appid), "l": steam_lang},
                    )
                    if r.status_code == 200:
                        node = (r.json() or {}).get(str(appid)) or {}
                        if node.get("success"):
                            return node.get("data") or {}
                except httpx.HTTPError:
                    pass
                return {}

        details = await asyncio.gather(*[_fetch_steam_detail(g["appid"]) for g in new_games])

    imported: list[dict] = []
    for g, detail in zip(new_games, details):
        appid = str(g["appid"])
        pos = g.get("positive") or 0
        neg = g.get("negative") or 0
        rating = round((pos / (pos + neg)) * 10.0, 1) if (pos + neg) > 0 else 8.0
        genres = [x["description"] for x in (detail.get("genres") or [])[:4] if x.get("description")]
        desc_text = clean_html_markup(detail.get("short_description") or "")
        cover = f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg"
        canonical = g.get("name") or "Untitled Game"

        media = await upsert_media_from_external(
            db,
            provider="steam",
            external_id=appid,
            external_url=f"https://store.steampowered.com/app/{appid}",
            media_type="game",
            canonical_title=canonical,
            rating=rating,
            popularity=pos,
            cover_url=cover,
            unit_label="hr",
            genres=genres or ["Action", "PC Game"],
            platforms=["PC"],
            synopsis_en=desc_text if locale == "en" else "",
            title_ru=canonical,
            synopsis_ru=desc_text if locale == "ru" else "",
            target_locale=locale,
        )
        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": media.canonical_title,
                "media_type": "game",
                "characters_linked": 0,
            }
        )

    has_next = not (steam_page > 0 and start + count >= len(all_games) and len(all_games) < 1000)
    if has_next:
        cursor.next_page += 1
    cursor.total_imported += len(imported)
    db.commit()
    return {
        "category": "game",
        "page_imported": offset_idx + 1,
        "next_page": cursor.next_page,
        "has_next_page": has_next,
        "total_imported": cursor.total_imported,
        "imported_count": len(imported),
        "items": imported,
    }


async def import_batch_from_series(db: Session, count: int = 10, locale: str = "ru") -> dict:
    """Batch import TV series & animated shows + cast from TVMaze public API."""
    cursor = db.scalar(
        select(SyncCursor).where(SyncCursor.provider == "tvmaze", SyncCursor.category == "series")
    )
    if not cursor:
        cursor = SyncCursor(provider="tvmaze", category="series", next_page=1, total_imported=0)
        db.add(cursor)
        db.flush()

    batch_index = cursor.next_page - 1
    tvmaze_page = (batch_index * count) // 240
    slice_offset = (batch_index * count) % 240

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get("https://api.tvmaze.com/shows", params={"page": tvmaze_page})
        if resp.status_code == 404:
            return {
                "category": "series",
                "page_imported": cursor.next_page,
                "next_page": cursor.next_page,
                "has_next_page": False,
                "total_imported": cursor.total_imported,
                "imported_count": 0,
                "items": [],
            }
        resp.raise_for_status()
        shows = resp.json() or []
        slice_shows = shows[slice_offset : slice_offset + count]
        if not slice_shows:
            if shows:
                cursor.next_page = ((tvmaze_page + 1) * 240 + count - 1) // count + 1
                db.commit()
                return {
                    "category": "series",
                    "page_imported": batch_index + 1,
                    "next_page": cursor.next_page,
                    "has_next_page": True,
                    "total_imported": cursor.total_imported,
                    "imported_count": 0,
                    "items": [],
                }
            return {
                "category": "series",
                "page_imported": cursor.next_page,
                "next_page": cursor.next_page,
                "has_next_page": False,
                "total_imported": cursor.total_imported,
                "imported_count": 0,
                "items": [],
            }

        show_ids = [str(s["id"]) for s in slice_shows]
        existing_ids = set(
            db.scalars(
                select(ExternalMapping.external_id).where(
                    ExternalMapping.entity_type == "media",
                    ExternalMapping.provider == "tvmaze",
                    ExternalMapping.external_id.in_(show_ids) if show_ids else False,
                )
            ).all()
        ) if show_ids else set()
        new_shows = [s for s in slice_shows if str(s["id"]) not in existing_ids]

        sem = asyncio.Semaphore(8)

        async def _fetch_cast(show_id: int) -> list[dict]:
            async with sem:
                try:
                    cr = await client.get(f"https://api.tvmaze.com/shows/{show_id}/cast")
                    if cr.status_code == 200:
                        return (cr.json() or [])[:3]
                except httpx.HTTPError:
                    pass
                return []

        cast_lists = await asyncio.gather(*[_fetch_cast(s["id"]) for s in new_shows])

    loc_sem = asyncio.Semaphore(14)

    async def _loc_item(title_s: str, desc_s: str, mtype: str = "") -> tuple[str, str]:
        async with loc_sem:
            return await _translate_pair_if_needed(title_s, desc_s, locale, media_type=mtype)

    show_locs = await asyncio.gather(
        *[_loc_item(s.get("name") or "Untitled Series", s.get("summary") or "", "series") for s in new_shows]
    )
    flat_cast = [(i, j, entry) for i, cast in enumerate(cast_lists) for j, entry in enumerate(cast) if (entry.get("character") or {}).get("id")]
    cast_locs = await asyncio.gather(
        *[_loc_item((entry.get("character") or {}).get("name") or "Unknown", "") for _, _, entry in flat_cast]
    )
    cast_loc_map = {(i, j): loc[0] for (i, j, _), loc in zip(flat_cast, cast_locs)}

    imported: list[dict] = []
    for i, (s, cast, (t_ru, d_ru)) in enumerate(zip(new_shows, cast_lists, show_locs)):
        show_id = str(s["id"])
        canonical = s.get("name") or "Untitled Series"
        premiered = s.get("premiered") or ""
        year = int(premiered[:4]) if premiered[:4].isdigit() else None
        rating = float((s.get("rating") or {}).get("average") or 0.0)
        img = (s.get("image") or {}).get("original") or (s.get("image") or {}).get("medium") or ""

        media = await upsert_media_from_external(
            db,
            provider="tvmaze",
            external_id=show_id,
            external_url=s.get("url") or "",
            media_type="series",
            canonical_title=canonical,
            release_year=year,
            status="completed" if s.get("status") == "Ended" else "ongoing",
            rating=round(rating, 1),
            popularity=s.get("weight") or 0,
            cover_url=img,
            unit_label="ep",
            genres=s.get("genres") or [],
            synopsis_en=s.get("summary") or "",
            title_ru=t_ru,
            synopsis_ru=d_ru,
            target_locale=locale,
        )

        char_count = 0
        for j, entry in enumerate(cast):
            ch = entry.get("character") or {}
            person = entry.get("person") or {}
            if not ch.get("id"):
                continue
            cname = ch.get("name") or "Unknown"
            c_img = (ch.get("image") or {}).get("original") or (ch.get("image") or {}).get("medium") or ""
            await upsert_character_from_external(
                db,
                provider="tvmaze",
                external_id=str(ch["id"]),
                external_url=ch.get("url") or "",
                canonical_name=cname,
                gender=(person.get("gender") or "unknown").lower(),
                image_url=c_img,
                name_ru=cast_loc_map.get((i, j), cname),
                translate_bio=False,
                target_locale=locale,
                media_id=media.id,
                role="main",
                voice_actor=person.get("name") or "",
            )
            char_count += 1

        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": (t_ru or canonical) if locale == "ru" else canonical,
                "media_type": "series",
                "characters_linked": char_count,
            }
        )

    cursor.next_page += 1
    cursor.total_imported += len(imported)
    db.commit()
    return {
        "category": "series",
        "page_imported": cursor.next_page - 1,
        "next_page": cursor.next_page,
        "has_next_page": len(shows) > 0,
        "total_imported": cursor.total_imported,
        "imported_count": len(imported),
        "items": imported,
    }


async def run_batch_import(db: Session, category: str, count: int = 10, locale: str = "ru") -> dict:
    """Dispatch batch import across all 8 Kiroku media categories."""
    if category in ("anime", "manga", "manhwa", "manhua", "novel"):
        return await import_batch_from_anilist(db, category=category, per_page=count, locale=locale)
    if category == "vn":
        return await import_batch_from_vndb(db, count=count, locale=locale)
    if category == "game":
        return await import_batch_from_games(db, count=count, locale=locale)
    if category == "series":
        return await import_batch_from_series(db, count=count, locale=locale)
    raise ValueError(f"Unsupported category: {category}")
