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


async def import_batch_from_vndb(db: Session, count: int = 10) -> dict:
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

        sem = asyncio.Semaphore(5)

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

        char_lists = await asyncio.gather(*[_fetch_chars(vn["id"]) for vn in vns])

    imported: list[dict] = []
    for vn, chars in zip(vns, char_lists):
        vn_id = vn["id"]
        exists = db.scalar(
            select(ExternalMapping.id).where(
                ExternalMapping.entity_type == "media",
                ExternalMapping.provider == "vndb",
                ExternalMapping.external_id == vn_id,
            )
        )
        if exists:
            continue

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
            canonical_title=vn.get("title") or "Untitled VN",
            native_title=vn.get("alttitle") or "",
            release_year=year,
            rating=rating,
            popularity=vn.get("votecount") or 0,
            cover_url=image_url,
            unit_label="hr",
            genres=tags,
            platforms=["PC"],
            synopsis_en=vn.get("description") or "",
        )

        char_count = 0
        for ch in chars:
            sex_raw = ch.get("sex")
            gender = {"m": "male", "f": "female"}.get(sex_raw[0], "unknown") if sex_raw else "unknown"
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
                translate_bio=False,
                media_id=media.id,
                role="main" if char_count == 0 else "supporting",
            )
            char_count += 1

        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": media.canonical_title,
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


async def import_batch_from_games(db: Session, count: int = 10) -> dict:
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
    async with httpx.AsyncClient(timeout=15.0) as client:
        sp_resp = await client.get(
            "https://steamspy.com/api.php",
            params={"request": "top100forever" if steam_page == 0 else "all", "page": steam_page},
        )
        sp_resp.raise_for_status()
        all_games = list((sp_resp.json() or {}).values())

        start = (offset_idx % 10) * count
        slice_games = all_games[start : start + count]

        sem = asyncio.Semaphore(5)

        async def _fetch_steam_detail(appid: int) -> dict:
            async with sem:
                try:
                    r = await client.get(
                        "https://store.steampowered.com/api/appdetails",
                        params={"appids": str(appid), "l": "russian"},
                    )
                    if r.status_code == 200:
                        node = (r.json() or {}).get(str(appid)) or {}
                        if node.get("success"):
                            return node.get("data") or {}
                except httpx.HTTPError:
                    pass
                return {}

        details = await asyncio.gather(*[_fetch_steam_detail(g["appid"]) for g in slice_games if g.get("appid")])

    imported: list[dict] = []
    for g, detail in zip(slice_games, details):
        appid = str(g["appid"])
        exists = db.scalar(
            select(ExternalMapping.id).where(
                ExternalMapping.entity_type == "media",
                ExternalMapping.provider == "steam",
                ExternalMapping.external_id == appid,
            )
        )
        if exists:
            continue

        pos = g.get("positive") or 0
        neg = g.get("negative") or 0
        rating = round((pos / (pos + neg)) * 10.0, 1) if (pos + neg) > 0 else 8.0
        genres = [x["description"] for x in (detail.get("genres") or [])[:4] if x.get("description")]
        ru_desc = clean_html_markup(detail.get("short_description") or "")
        cover = f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg"

        media = await upsert_media_from_external(
            db,
            provider="steam",
            external_id=appid,
            external_url=f"https://store.steampowered.com/app/{appid}",
            media_type="game",
            canonical_title=g.get("name") or "Untitled Game",
            rating=rating,
            popularity=pos,
            cover_url=cover,
            unit_label="hr",
            genres=genres or ["Action", "PC Game"],
            platforms=["PC"],
            synopsis_en=ru_desc,
            title_ru=g.get("name") or "Untitled Game",
            synopsis_ru=ru_desc,
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

    cursor.next_page += 1
    cursor.total_imported += len(imported)
    db.commit()
    return {
        "category": "game",
        "page_imported": cursor.next_page - 1,
        "next_page": cursor.next_page,
        "has_next_page": len(all_games) > 0,
        "total_imported": cursor.total_imported,
        "imported_count": len(imported),
        "items": imported,
    }


async def import_batch_from_series(db: Session, count: int = 10) -> dict:
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
        resp.raise_for_status()
        shows = resp.json() or []
        slice_shows = shows[slice_offset : slice_offset + count]

        sem = asyncio.Semaphore(5)

        async def _fetch_cast(show_id: int) -> list[dict]:
            async with sem:
                try:
                    cr = await client.get(f"https://api.tvmaze.com/shows/{show_id}/cast")
                    if cr.status_code == 200:
                        return (cr.json() or [])[:3]
                except httpx.HTTPError:
                    pass
                return []

        cast_lists = await asyncio.gather(*[_fetch_cast(s["id"]) for s in slice_shows])

    imported: list[dict] = []
    for s, cast in zip(slice_shows, cast_lists):
        show_id = str(s["id"])
        exists = db.scalar(
            select(ExternalMapping.id).where(
                ExternalMapping.entity_type == "media",
                ExternalMapping.provider == "tvmaze",
                ExternalMapping.external_id == show_id,
            )
        )
        if exists:
            continue

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
            canonical_title=s.get("name") or "Untitled Series",
            release_year=year,
            status="completed" if s.get("status") == "Ended" else "ongoing",
            rating=round(rating, 1),
            popularity=s.get("weight") or 0,
            cover_url=img,
            unit_label="ep",
            genres=s.get("genres") or [],
            synopsis_en=s.get("summary") or "",
        )

        char_count = 0
        for entry in cast:
            ch = entry.get("character") or {}
            person = entry.get("person") or {}
            if not ch.get("id"):
                continue
            c_img = (ch.get("image") or {}).get("original") or (ch.get("image") or {}).get("medium") or ""
            await upsert_character_from_external(
                db,
                provider="tvmaze",
                external_id=str(ch["id"]),
                external_url=ch.get("url") or "",
                canonical_name=ch.get("name") or "Unknown",
                gender=(person.get("gender") or "unknown").lower(),
                image_url=c_img,
                name_ru=ch.get("name") or "Unknown",
                translate_bio=False,
                media_id=media.id,
                role="main",
                voice_actor=person.get("name") or "",
            )
            char_count += 1

        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": media.canonical_title,
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


async def run_batch_import(db: Session, category: str, count: int = 10) -> dict:
    """Dispatch batch import across all 8 Kiroku media categories."""
    if category in ("anime", "manga", "manhwa", "manhua", "novel"):
        return await import_batch_from_anilist(db, category=category, per_page=count)
    if category == "vn":
        return await import_batch_from_vndb(db, count=count)
    if category == "game":
        return await import_batch_from_games(db, count=count)
    if category == "series":
        return await import_batch_from_series(db, count=count)
    raise ValueError(f"Unsupported category: {category}")
