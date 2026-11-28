import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.importers.base import upsert_character_from_external, upsert_media_from_external


async def import_from_vndb(db: Session, query: str, limit: int = 5, locale: str = "ru") -> list[dict]:
    """Query VNDB Kana API v2 (no auth required) for visual novels and characters."""
    import asyncio
    from app.services.translation import translate_text
    from app.services.importers.base import clean_html_markup

    settings = get_settings()
    async with httpx.AsyncClient(timeout=20.0) as client:
        vn_resp = await client.post(
            f"{settings.vndb_api_url}/vn",
            json={
                "filters": ["search", "=", query],
                "fields": "id, title, alttitle, released, rating, votecount, description, image.url, tags.name",
                "sort": "votecount",
                "reverse": True,
                "results": limit,
            },
        )
        vn_resp.raise_for_status()
        vns = vn_resp.json().get("results") or []

        sem = asyncio.Semaphore(8)

        async def _fetch_chars(vn_id: str) -> list[dict]:
            async with sem:
                cr = await client.post(
                    f"{settings.vndb_api_url}/character",
                    json={
                        "filters": ["vn", "=", ["id", "=", vn_id]],
                        "fields": "id, name, original, description, image.url, sex, vns.role",
                        "results": 6,
                    },
                )
                return (cr.json().get("results") or []) if cr.status_code == 200 else []

        char_lists = await asyncio.gather(*[_fetch_chars(vn["id"]) for vn in vns])

    async def _loc_pair(title_en: str, desc_en: str) -> tuple[str, str]:
        if locale != "ru":
            return title_en, ""
        c_desc = clean_html_markup(desc_en)[:1200]
        t_c = translate_text(title_en, "ru") if title_en else asyncio.sleep(0, result=("", "api_raw"))
        d_c = translate_text(c_desc, "ru") if c_desc else asyncio.sleep(0, result=("", "api_raw"))
        (tr_t, _), (tr_d, _) = await asyncio.gather(t_c, d_c)
        return (tr_t or title_en), tr_d

    vn_locs = await asyncio.gather(*[_loc_pair(vn.get("title") or "Untitled VN", vn.get("description") or "") for vn in vns])
    flat_chars = [(i, j, ch) for i, chars in enumerate(char_lists) for j, ch in enumerate(chars)]
    ch_locs = await asyncio.gather(*[_loc_pair(ch.get("name") or "Unknown", ch.get("description") or "") for _, _, ch in flat_chars])
    ch_loc_map = {(i, j): loc for (i, j, _), loc in zip(flat_chars, ch_locs)}

    imported: list[dict] = []
    for i, (vn, chars, (t_ru, d_ru)) in enumerate(zip(vns, char_lists, vn_locs)):
        vn_id = vn["id"]
        canonical = vn.get("title") or "Untitled VN"
        year = int(vn["released"][:4]) if vn.get("released") and vn["released"][:4].isdigit() else None
        rating = round(vn["rating"] / 10.0, 1) if vn.get("rating") else 0.0
        tags = [t["name"] for t in (vn.get("tags") or [])[:6] if t.get("name")]
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
            status="completed",
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
                role="main" if char_count < 2 else "supporting",
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

    db.commit()
    return imported
