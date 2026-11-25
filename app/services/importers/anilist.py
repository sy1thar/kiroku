import asyncio
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SyncCursor
from app.services.importers.base import clean_html_markup, upsert_character_from_external, upsert_media_from_external
from app.services.translation import fetch_shikimori_character_ru, fetch_shikimori_ru, translate_text

ANILIST_SEARCH_QUERY = """
query ($search: String, $type: MediaType, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: $type, sort: POPULARITY_DESC) {
      id
      idMal
      siteUrl
      type
      format
      countryOfOrigin
      status
      seasonYear
      startDate { year }
      episodes
      chapters
      averageScore
      popularity
      genres
      title {
        romaji
        english
        native
      }
      description(asHtml: false)
      coverImage {
        extraLarge
        large
      }
      bannerImage
      characters(sort: [ROLE, FAVOURITES_DESC], perPage: 8) {
        edges {
          role
          voiceActors(language: JAPANESE, sort: FAVOURITES_DESC) {
            name { full }
          }
          node {
            id
            siteUrl
            name { full native }
            gender
            description(asHtml: false)
            favourites
            image { large }
          }
        }
      }
    }
  }
}
"""


def _map_anilist_type(item: dict) -> tuple[str, int | None, str]:
    raw_type = item.get("type", "ANIME")
    fmt = item.get("format", "")
    country = item.get("countryOfOrigin", "JP")

    if raw_type == "ANIME":
        return "anime", item.get("episodes"), "ep"
    if fmt == "NOVEL":
        return "novel", item.get("chapters"), "ch"
    if country == "KR":
        return "manhwa", item.get("chapters"), "ch"
    if country == "CN":
        return "manhua", item.get("chapters"), "ch"
    return "manga", item.get("chapters"), "ch"


def _map_anilist_status(raw: str | None) -> str:
    return {
        "RELEASING": "ongoing",
        "FINISHED": "completed",
        "NOT_YET_RELEASED": "upcoming",
    }.get(raw or "", "completed")


async def import_from_anilist(
    db: Session,
    query: str,
    media_type: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Query AniList GraphQL API and persist matching media & characters into Kiroku."""
    settings = get_settings()
    gql_type = None
    if media_type in ("anime", "ANIME"):
        gql_type = "ANIME"
    elif media_type in ("manga", "manhwa", "manhua", "novel", "MANGA"):
        gql_type = "MANGA"

    variables = {"search": query, "type": gql_type, "perPage": limit}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            settings.anilist_api_url,
            json={"query": ANILIST_SEARCH_QUERY, "variables": variables},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    items = ((data.get("data") or {}).get("Page") or {}).get("media") or []
    imported: list[dict] = []

    for item in items:
        titles = item.get("title") or {}
        canonical = titles.get("english") or titles.get("romaji") or titles.get("native") or "Untitled"
        m_type, units, unit_label = _map_anilist_type(item)
        year = item.get("seasonYear") or (item.get("startDate") or {}).get("year")
        avg_score = item.get("averageScore")
        rating = round((avg_score / 10.0), 1) if avg_score else 0.0
        cover = (item.get("coverImage") or {}).get("extraLarge") or (item.get("coverImage") or {}).get("large") or ""
        shiki_kind = "animes" if m_type == "anime" else "mangas"
        ru_title, ru_desc = await fetch_shikimori_ru(
            kind=shiki_kind,
            mal_id=item.get("idMal"),
            search_title=titles.get("romaji") or canonical,
        )

        media = await upsert_media_from_external(
            db,
            provider="anilist",
            external_id=str(item["id"]),
            external_url=item.get("siteUrl") or "",
            media_type=m_type,
            canonical_title=canonical,
            native_title=titles.get("native") or "",
            release_year=year,
            status=_map_anilist_status(item.get("status")),
            rating=rating,
            popularity=item.get("popularity") or 0,
            cover_url=cover,
            banner_url=item.get("bannerImage") or "",
            total_units=units,
            unit_label=unit_label,
            genres=item.get("genres") or [],
            synopsis_en=item.get("description") or "",
            title_ru=ru_title,
            synopsis_ru=ru_desc,
        )

        char_count = 0
        for edge in (item.get("characters") or {}).get("edges") or []:
            node = edge.get("node") or {}
            if not node.get("id"):
                continue
            char_name = (node.get("name") or {}).get("full") or "Unknown"
            va_list = edge.get("voiceActors") or []
            va_name = (va_list[0].get("name") or {}).get("full", "") if va_list else ""
            raw_role = (edge.get("role") or "SUPPORTING").lower()
            role = "main" if raw_role == "main" else "supporting"

            await upsert_character_from_external(
                db,
                provider="anilist",
                external_id=str(node["id"]),
                external_url=node.get("siteUrl") or "",
                canonical_name=char_name,
                native_name=(node.get("name") or {}).get("native") or "",
                gender=(node.get("gender") or "unknown").lower(),
                image_url=(node.get("image") or {}).get("large") or "",
                bio_en=node.get("description") or "",
                popularity=node.get("favourites") or 0,
                media_id=media.id,
                role=role,
                voice_actor=va_name,
            )
            char_count += 1

        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": media.canonical_title,
                "media_type": media.media_type,
                "characters_linked": char_count,
            }
        )

    db.commit()
    return imported


ANILIST_BATCH_QUERY_BASE = """
query ($page: Int, $perPage: Int, $type: MediaType{extra_decl}) {{
  Page(page: $page, perPage: $perPage) {{
    pageInfo {{
      currentPage
      hasNextPage
    }}
    media(type: $type{extra_arg}, sort: POPULARITY_DESC, isAdult: false) {{
      id
      idMal
      siteUrl
      type
      format
      countryOfOrigin
      status
      seasonYear
      startDate {{ year }}
      episodes
      chapters
      averageScore
      popularity
      genres
      title {{
        romaji
        english
        native
      }}
      description(asHtml: false)
      coverImage {{
        extraLarge
        large
      }}
      bannerImage
      characters(sort: [ROLE, FAVOURITES_DESC], perPage: 3) {{
        edges {{
          role
          voiceActors(language: JAPANESE, sort: FAVOURITES_DESC) {{
            name {{ full }}
          }}
          node {{
            id
            siteUrl
            name {{ full native }}
            gender
            description(asHtml: false)
            favourites
            image {{ large }}
          }}
        }}
      }}
    }}
  }}
}}
"""


async def import_batch_from_anilist(
    db: Session,
    category: str = "anime",
    per_page: int = 10,
) -> dict:
    """
    Continuously import the next batch of NEW titles for 'anime', 'manga', 'manhwa', 'manhua', or 'novel'.
    Uses concurrent Shikimori lookups to stay well under cloud HTTP request timeouts.
    """
    from app.models import ExternalMapping

    settings = get_settings()
    cursor = db.scalar(
        select(SyncCursor).where(
            SyncCursor.provider == "anilist",
            SyncCursor.category == category,
        )
    )
    if not cursor:
        cursor = SyncCursor(provider="anilist", category=category, next_page=1, total_imported=0)
        db.add(cursor)
        db.flush()

    gql_type = "ANIME" if category == "anime" else "MANGA"
    country_map = {"manga": "JP", "manhwa": "KR", "manhua": "CN"}
    country = country_map.get(category)
    fmt = "NOVEL" if category == "novel" else None

    decls: list[str] = []
    args: list[str] = []
    if country:
        decls.append("$country: CountryCode")
        args.append("countryOfOrigin: $country")
    if fmt:
        decls.append("$format: MediaFormat")
        args.append("format: $format")

    gql_query = ANILIST_BATCH_QUERY_BASE.format(
        extra_decl=(", " + ", ".join(decls)) if decls else "",
        extra_arg=(", " + ", ".join(args)) if args else "",
    )

    page_num = cursor.next_page
    candidates: list[dict] = []
    has_next = True
    pages_fetched = 0
    fetch_size = max(per_page, 20)

    async with httpx.AsyncClient(timeout=15.0) as client:
        while len(candidates) < per_page and has_next and pages_fetched < 5:
            variables: dict = {"page": page_num, "perPage": fetch_size, "type": gql_type}
            if country:
                variables["country"] = country
            if fmt:
                variables["format"] = fmt

            resp = await client.post(
                settings.anilist_api_url,
                json={"query": gql_query, "variables": variables},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            payload = resp.json()

            page_data = (payload.get("data") or {}).get("Page") or {}
            page_info = page_data.get("pageInfo") or {}
            items = page_data.get("media") or []
            has_next = bool(page_info.get("hasNextPage"))
            pages_fetched += 1

            for item in items:
                if len(candidates) >= per_page:
                    break
                ext_id = str(item["id"])
                already_exists = db.scalar(
                    select(ExternalMapping.id).where(
                        ExternalMapping.entity_type == "media",
                        ExternalMapping.provider == "anilist",
                        ExternalMapping.external_id == ext_id,
                    )
                )
                if not already_exists:
                    candidates.append(item)

            if has_next and len(candidates) < per_page:
                page_num += 1
            else:
                break

    sem = asyncio.Semaphore(10)

    async def _resolve_media_ru(item: dict) -> tuple[str, str]:
        titles = item.get("title") or {}
        canonical = titles.get("english") or titles.get("romaji") or titles.get("native") or "Untitled"
        m_type, _, _ = _map_anilist_type(item)
        shiki_kind = "animes" if m_type == "anime" else "mangas"
        async with sem:
            ru_title, ru_desc = await fetch_shikimori_ru(
                kind=shiki_kind,
                mal_id=item.get("idMal"),
                search_title=titles.get("romaji") or canonical,
            )
            if not ru_title:
                ru_title, _ = await translate_text(canonical, "ru")
            if not ru_desc and item.get("description"):
                ru_desc, _ = await translate_text(clean_html_markup(item.get("description")), "ru")
            return ru_title or canonical, ru_desc

    async def _resolve_char_ru(name: str) -> str:
        async with sem:
            ru_name = await fetch_shikimori_character_ru(name)
            if not ru_name:
                ru_name, _ = await translate_text(name, "ru")
            return ru_name or name

    media_ru_list = await asyncio.gather(*[_resolve_media_ru(it) for it in candidates])

    char_names_to_lookup: list[str] = []
    for item in candidates:
        for edge in (item.get("characters") or {}).get("edges") or []:
            node = edge.get("node") or {}
            cname = (node.get("name") or {}).get("full")
            if cname:
                char_names_to_lookup.append(cname)
    unique_names = list(dict.fromkeys(char_names_to_lookup))
    char_ru_results = await asyncio.gather(*[_resolve_char_ru(n) for n in unique_names])
    char_ru_map = dict(zip(unique_names, char_ru_results))

    imported: list[dict] = []
    for item, (ru_title, ru_desc) in zip(candidates, media_ru_list):
        titles = item.get("title") or {}
        canonical = titles.get("english") or titles.get("romaji") or titles.get("native") or "Untitled"
        m_type, units, unit_label = _map_anilist_type(item)
        year = item.get("seasonYear") or (item.get("startDate") or {}).get("year")
        avg_score = item.get("averageScore")
        rating = round((avg_score / 10.0), 1) if avg_score else 0.0
        cover = (item.get("coverImage") or {}).get("extraLarge") or (item.get("coverImage") or {}).get("large") or ""

        media = await upsert_media_from_external(
            db,
            provider="anilist",
            external_id=str(item["id"]),
            external_url=item.get("siteUrl") or "",
            media_type=m_type,
            canonical_title=canonical,
            native_title=titles.get("native") or "",
            release_year=year,
            status=_map_anilist_status(item.get("status")),
            rating=rating,
            popularity=item.get("popularity") or 0,
            cover_url=cover,
            banner_url=item.get("bannerImage") or "",
            total_units=units,
            unit_label=unit_label,
            genres=item.get("genres") or [],
            synopsis_en=item.get("description") or "",
            title_ru=ru_title,
            synopsis_ru=ru_desc,
        )

        char_count = 0
        for edge in (item.get("characters") or {}).get("edges") or []:
            node = edge.get("node") or {}
            if not node.get("id"):
                continue
            char_name = (node.get("name") or {}).get("full") or "Unknown"
            va_list = edge.get("voiceActors") or []
            va_name = (va_list[0].get("name") or {}).get("full", "") if va_list else ""
            raw_role = (edge.get("role") or "SUPPORTING").lower()
            role = "main" if raw_role == "main" else "supporting"

            await upsert_character_from_external(
                db,
                provider="anilist",
                external_id=str(node["id"]),
                external_url=node.get("siteUrl") or "",
                canonical_name=char_name,
                native_name=(node.get("name") or {}).get("native") or "",
                gender=(node.get("gender") or "unknown").lower(),
                image_url=(node.get("image") or {}).get("large") or "",
                bio_en=node.get("description") or "",
                name_ru=char_ru_map.get(char_name, ""),
                translate_bio=False,
                popularity=node.get("favourites") or 0,
                media_id=media.id,
                role=role,
                voice_actor=va_name,
            )
            char_count += 1

        imported.append(
            {
                "id": media.id,
                "slug": media.slug,
                "title": media.canonical_title,
                "media_type": media.media_type,
                "characters_linked": char_count,
            }
        )

    has_next_out = has_next
    cursor.next_page = page_num + 1 if has_next_out else page_num
    cursor.total_imported += len(imported)
    db.commit()

    return {
        "category": category,
        "page_imported": page_num,
        "next_page": cursor.next_page,
        "has_next_page": has_next_out,
        "total_imported": cursor.total_imported,
        "imported_count": len(imported),
        "items": imported,
    }
