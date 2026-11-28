from datetime import datetime, timezone
import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.importers.base import upsert_character_from_external, upsert_media_from_external


class IGDBNotConfiguredError(RuntimeError):
    """Raised when TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set."""


async def _get_twitch_token(client: httpx.AsyncClient, client_id: str, client_secret: str) -> str:
    resp = await client.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def import_from_igdb(db: Session, query: str, limit: int = 5, locale: str = "ru") -> list[dict]:
    """Query IGDB v4 API (games + characters) using Twitch Client Credentials."""
    import asyncio
    from app.services.translation import translate_text
    from app.services.importers.base import clean_html_markup

    settings = get_settings()
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise IGDBNotConfiguredError(
            "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are required for live IGDB imports."
        )

    async with httpx.AsyncClient(timeout=20.0) as client:
        token = await _get_twitch_token(client, settings.twitch_client_id, settings.twitch_client_secret)
        headers = {
            "Client-ID": settings.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }
        igdb_query = (
            f'search "{query}"; '
            "fields name, slug, summary, first_release_date, total_rating, total_rating_count, "
            "cover.image_id, genres.name, platforms.name, url; "
            f"limit {limit};"
        )
        games_resp = await client.post("https://api.igdb.com/v4/games", headers=headers, content=igdb_query)
        games_resp.raise_for_status()
        games = games_resp.json()

        sem = asyncio.Semaphore(6)

        async def _fetch_chars(gid: int) -> list[dict]:
            async with sem:
                cq = f"fields name, description, gender, mug_shot.image_id, url; where games = ({gid}); limit 8;"
                cr = await client.post("https://api.igdb.com/v4/characters", headers=headers, content=cq)
                return cr.json() if cr.status_code == 200 else []

        char_lists = await asyncio.gather(*[_fetch_chars(g["id"]) for g in games])

    async def _loc(t: str, d: str) -> tuple[str, str]:
        if locale != "ru":
            return t, ""
        cd = clean_html_markup(d)[:1000]
        tc = translate_text(t, "ru") if t else asyncio.sleep(0, result=("", "api_raw"))
        dc = translate_text(cd, "ru") if cd else asyncio.sleep(0, result=("", "api_raw"))
        (rt, _), (rd, _) = await asyncio.gather(tc, dc)
        return (rt or t), rd

    game_locs = await asyncio.gather(*[_loc(g.get("name") or "Untitled Game", g.get("summary") or "") for g in games])

    imported: list[dict] = []
    for g, chars, (t_ru, d_ru) in zip(games, char_lists, game_locs):
        game_id = g["id"]
        canonical = g.get("name") or "Untitled Game"
        ts = g.get("first_release_date")
        year = datetime.fromtimestamp(ts, tz=timezone.utc).year if ts else None
        rating = round(g["total_rating"] / 10.0, 1) if g.get("total_rating") else 0.0
        cover_id = (g.get("cover") or {}).get("image_id")
        cover_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover_id}.jpg" if cover_id else ""
        genres = [item["name"] for item in (g.get("genres") or []) if item.get("name")]
        platforms = [item["name"] for item in (g.get("platforms") or []) if item.get("name")]

        media = await upsert_media_from_external(
            db,
            provider="igdb",
            external_id=str(game_id),
            external_url=g.get("url") or "",
            media_type="game",
            canonical_title=canonical,
            release_year=year,
            rating=rating,
            popularity=g.get("total_rating_count") or 0,
            cover_url=cover_url,
            unit_label="hr",
            genres=genres,
            platforms=platforms,
            synopsis_en=g.get("summary") or "",
            title_ru=t_ru,
            synopsis_ru=d_ru,
            target_locale=locale,
        )

        ch_locs = await asyncio.gather(*[_loc(ch.get("name") or "Unknown", ch.get("description") or "") for ch in chars])
        char_count = 0
        for ch, (cn_ru, cb_ru) in zip(chars, ch_locs):
            mug_id = (ch.get("mug_shot") or {}).get("image_id")
            img_url = f"https://images.igdb.com/igdb/image/upload/t_thumb/{mug_id}.jpg" if mug_id else ""
            gender = {0: "male", 1: "female", 2: "other"}.get(ch.get("gender"), "unknown")
            await upsert_character_from_external(
                db,
                provider="igdb",
                external_id=str(ch["id"]),
                external_url=ch.get("url") or "",
                canonical_name=ch.get("name") or "Unknown",
                gender=gender,
                image_url=img_url,
                bio_en=ch.get("description") or "",
                name_ru=cn_ru,
                bio_ru=cb_ru,
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
                "media_type": "game",
                "characters_linked": char_count,
            }
        )

    db.commit()
    return imported
