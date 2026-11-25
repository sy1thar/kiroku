import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.importers.base import upsert_character_from_external, upsert_media_from_external


async def import_from_vndb(db: Session, query: str, limit: int = 5) -> list[dict]:
    """Query VNDB Kana API v2 (no auth required) for visual novels and characters."""
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

        imported: list[dict] = []
        for vn in vns:
            vn_id = vn["id"]
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
                canonical_title=vn.get("title") or "Untitled VN",
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
            )

            char_resp = await client.post(
                f"{settings.vndb_api_url}/character",
                json={
                    "filters": ["vn", "=", ["id", "=", vn_id]],
                    "fields": "id, name, original, description, image.url, sex, vns.role",
                    "results": 6,
                },
            )
            char_count = 0
            if char_resp.status_code == 200:
                for ch in char_resp.json().get("results") or []:
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
                        media_id=media.id,
                        role="main" if char_count < 2 else "supporting",
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

    db.commit()
    return imported
