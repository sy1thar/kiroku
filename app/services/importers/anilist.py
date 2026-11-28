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
    locale: str = "ru",
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

    sem = asyncio.Semaphore(12)

    async def _res_media(item: dict) -> tuple[str, str]:
        if locale != "ru":
            return "", ""
        titles = item.get("title") or {}
        canonical = titles.get("english") or titles.get("romaji") or titles.get("native") or "Untitled"
        m_type, _, _ = _map_anilist_type(item)
        shiki_kind = "animes" if m_type == "anime" else "mangas"
        async with sem:
            ru_title, ru_desc = await fetch_shikimori_ru(
                kind=shiki_kind,
                mal_id=item.get("idMal"),
                search_title=titles.get("romaji") or canonical,
                alt_titles=[canonical, titles.get("romaji") or ""],
            )
            desc_clean = clean_html_markup(item.get("description"))
            t_coro = translate_text(canonical, "ru") if not ru_title else asyncio.sleep(0, result=(ru_title, "api_raw"))
            d_coro = translate_text(desc_clean[:1600], "ru") if (not ru_desc and desc_clean) else asyncio.sleep(0, result=(ru_desc, "api_raw"))
            (rt, _), (rd, _) = await asyncio.gather(t_coro, d_coro)
            return rt or canonical, rd

    async def _res_char(edge: dict) -> tuple[str, str]:
        if locale != "ru":
            return "", ""
        node = edge.get("node") or {}
        cname = (node.get("name") or {}).get("full") or ""
        cbio = clean_html_markup(node.get("description") or "")
        async with sem:
            rn = await fetch_shikimori_character_ru(cname)
            n_coro = translate_text(cname, "ru") if not rn else asyncio.sleep(0, result=(rn, "api_raw"))
            b_coro = translate_text(cbio[:900], "ru") if cbio else asyncio.sleep(0, result=("", "api_raw"))
            (r_name, _), (r_bio, _) = await asyncio.gather(n_coro, b_coro)
            return (r_name or cname), r_bio

    media_ru_list = await asyncio.gather(*[_res_media(it) for it in items])
    unique_edges: dict[str, dict] = {}
    for item in items:
        for edge in (item.get("characters") or {}).get("edges") or []:
            node = edge.get("node") or {}
            cname = (node.get("name") or {}).get("full")
            if cname and cname not in unique_edges:
                unique_edges[cname] = edge
    c_names = list(unique_edges.keys())
    c_results = await asyncio.gather(*[_res_char(unique_edges[n]) for n in c_names])
    c_map = dict(zip(c_names, c_results))

    imported: list[dict] = []
    for item, (ru_title, ru_desc) in zip(items, media_ru_list):
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
            target_locale=locale,
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
            c_rn, c_rb = c_map.get(char_name, ("", ""))

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
                name_ru=c_rn,
                bio_ru=c_rb,
                translate_bio=False,
                target_locale=locale,
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
                "title": (ru_title or canonical) if locale == "ru" else canonical,
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
    locale: str = "ru",
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

    sem = asyncio.Semaphore(14)

    async def _resolve_media_ru(item: dict) -> tuple[str, str]:
        if locale != "ru":
            return "", ""
        titles = item.get("title") or {}
        canonical = titles.get("english") or titles.get("romaji") or titles.get("native") or "Untitled"
        m_type, _, _ = _map_anilist_type(item)
        shiki_kind = "animes" if m_type == "anime" else "mangas"
        async with sem:
            ru_title, ru_desc = await fetch_shikimori_ru(
                kind=shiki_kind,
                mal_id=item.get("idMal"),
                search_title=titles.get("romaji") or canonical,
                alt_titles=[canonical, titles.get("romaji") or ""],
            )
            desc_clean = clean_html_markup(item.get("description"))
            t_coro = translate_text(canonical, "ru") if not ru_title else asyncio.sleep(0, result=(ru_title, "api_raw"))
            d_coro = translate_text(desc_clean[:1400], "ru") if (not ru_desc and desc_clean) else asyncio.sleep(0, result=(ru_desc, "api_raw"))
            (rt, _), (rd, _) = await asyncio.gather(t_coro, d_coro)
            return rt or canonical, rd

    async def _resolve_char_ru(edge: dict) -> tuple[str, str]:
        if locale != "ru":
            return "", ""
        node = edge.get("node") or {}
        name = (node.get("name") or {}).get("full") or ""
        bio_clean = clean_html_markup(node.get("description") or "")
        async with sem:
            ru_name = await fetch_shikimori_character_ru(name)
            n_coro = translate_text(name, "ru") if not ru_name else asyncio.sleep(0, result=(ru_name, "api_raw"))
            b_coro = translate_text(bio_clean[:900], "ru") if bio_clean else asyncio.sleep(0, result=("", "api_raw"))
            (rn, _), (rb, _) = await asyncio.gather(n_coro, b_coro)
            return (rn or name), rb

    media_ru_list = await asyncio.gather(*[_resolve_media_ru(it) for it in candidates])

    unique_edges_by_name: dict[str, dict] = {}
    candidate_cids: set[str] = set()
    for item in candidates:
        for edge in (item.get("characters") or {}).get("edges") or []:
            node = edge.get("node") or {}
            cname = (node.get("name") or {}).get("full")
            cid = str(node.get("id") or "")
            if cid:
                candidate_cids.add(cid)
            if cname and cname not in unique_edges_by_name:
                unique_edges_by_name[cname] = edge

    existing_char_cids = set(
        db.scalars(
            select(ExternalMapping.external_id).where(
                ExternalMapping.entity_type == "character",
                ExternalMapping.provider == "anilist",
                ExternalMapping.external_id.in_(candidate_cids) if candidate_cids else False,
            )
        ).all()
    ) if candidate_cids else set()

    to_translate_names = [
        n for n, edge in unique_edges_by_name.items()
        if str((edge.get("node") or {}).get("id") or "") not in existing_char_cids
    ]
    char_ru_results = await asyncio.gather(*[_resolve_char_ru(unique_edges_by_name[n]) for n in to_translate_names])
    char_ru_map = dict(zip(to_translate_names, char_ru_results))

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
            target_locale=locale,
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

            c_ru_name, c_ru_bio = char_ru_map.get(char_name, ("", ""))
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
                name_ru=c_ru_name,
                bio_ru=c_ru_bio,
                translate_bio=False,
                target_locale=locale,
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
                "title": (ru_title or canonical) if locale == "ru" else canonical,
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


ANILIST_MEDIA_CHARACTERS_QUERY = """
query ($id: Int, $page: Int, $perPage: Int) {
  Media(id: $id) {
    id
    characters(page: $page, perPage: $perPage, sort: [ROLE, FAVOURITES_DESC]) {
      pageInfo {
        hasNextPage
      }
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
"""


async def import_more_characters_for_media(
    db: Session,
    media_id: int,
    count: int = 12,
    locale: str = "ru",
    start_page: int = 1,
) -> dict:
    """Batch-import additional unlinked characters directly into an existing media title (Admin)."""
    from app.models import ExternalMapping, Media, MediaCharacter

    media = db.get(Media, media_id)
    if not media:
        raise ValueError("Media not found")

    mapping = db.scalar(
        select(ExternalMapping).where(
            ExternalMapping.entity_type == "media",
            ExternalMapping.entity_id == media.id,
        )
    )
    if not mapping:
        raise ValueError("This title has no external API mapping yet.")

    existing_char_ids = set(
        db.scalars(select(MediaCharacter.character_id).where(MediaCharacter.media_id == media.id)).all()
    )

    if mapping.provider == "anilist":
        settings = get_settings()
        new_edges: list[dict] = []
        page = max(1, start_page)
        max_page = page + 3
        has_next = True
        broke_mid_page = False
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(new_edges) < count and has_next and page <= max_page:
                resp = await client.post(
                    settings.anilist_api_url,
                    json={
                        "query": ANILIST_MEDIA_CHARACTERS_QUERY,
                        "variables": {"id": int(mapping.external_id), "page": page, "perPage": 25},
                    },
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                media_node = (resp.json().get("data") or {}).get("Media") or {}
                char_conn = media_node.get("characters") or {}
                has_next = bool((char_conn.get("pageInfo") or {}).get("hasNextPage"))
                for edge in char_conn.get("edges") or []:
                    node = edge.get("node") or {}
                    ext_cid = str(node.get("id") or "")
                    if not ext_cid:
                        continue
                    ext_map = db.scalar(
                        select(ExternalMapping).where(
                            ExternalMapping.entity_type == "character",
                            ExternalMapping.provider == "anilist",
                            ExternalMapping.external_id == ext_cid,
                        )
                    )
                    if ext_map and ext_map.entity_id in existing_char_ids:
                        continue
                    new_edges.append(edge)
                    if len(new_edges) >= count:
                        broke_mid_page = True
                        break
                if broke_mid_page:
                    break
                page += 1
        has_more = bool(broke_mid_page or has_next)

        sem = asyncio.Semaphore(14)

        async def _res_char(edge: dict) -> tuple[str, str]:
            node = edge.get("node") or {}
            cname = (node.get("name") or {}).get("full", "")
            cbio = clean_html_markup(node.get("description") or "")
            if locale != "ru":
                return cname, cbio
            async with sem:
                rn = await fetch_shikimori_character_ru(cname)
                n_coro = translate_text(cname, "ru") if not rn else asyncio.sleep(0, result=(rn, "api_raw"))
                b_coro = translate_text(cbio[:900], "ru") if cbio else asyncio.sleep(0, result=("", "api_raw"))
                (r_name, _), (r_bio, _) = await asyncio.gather(n_coro, b_coro)
                return (r_name or cname), r_bio

        resolved_chars = await asyncio.gather(*[_res_char(e) for e in new_edges])

        added: list[dict] = []
        for edge, (ru_name, ru_bio) in zip(new_edges, resolved_chars):
            node = edge.get("node") or {}
            cname = (node.get("name") or {}).get("full") or "Unknown"
            va_list = edge.get("voiceActors") or []
            va_name = (va_list[0].get("name") or {}).get("full", "") if va_list else ""
            raw_role = (edge.get("role") or "SUPPORTING").lower()
            role = "main" if raw_role == "main" else "supporting"

            ch = await upsert_character_from_external(
                db,
                provider="anilist",
                external_id=str(node["id"]),
                external_url=node.get("siteUrl") or "",
                canonical_name=cname,
                native_name=(node.get("name") or {}).get("native") or "",
                gender=(node.get("gender") or "unknown").lower(),
                image_url=(node.get("image") or {}).get("large") or "",
                bio_en=node.get("description") or "",
                name_ru=ru_name if locale == "ru" else cname,
                bio_ru=ru_bio if locale == "ru" else "",
                translate_bio=False,
                target_locale=locale,
                popularity=node.get("favourites") or 0,
                media_id=media.id,
                role=role,
                voice_actor=va_name,
            )
            added.append({"id": ch.id, "slug": ch.slug, "name": ru_name if locale == "ru" else cname})

        if not has_more:
            media.characters_exhausted = True
        db.commit()
        return {
            "media_id": media.id,
            "added_count": len(added),
            "next_page": page if broke_mid_page else page,
            "has_more": has_more,
            "media_exhausted": bool(media.characters_exhausted),
            "characters": added,
        }

    if mapping.provider == "vndb":
        settings = get_settings()
        page = max(1, start_page)
        async with httpx.AsyncClient(timeout=15.0) as client:
            cr = await client.post(
                f"{settings.vndb_api_url}/character",
                json={
                    "filters": ["vn", "=", ["id", "=", mapping.external_id]],
                    "fields": "id, name, original, description, image.url, sex",
                    "page": page,
                    "results": 25,
                },
            )
            cr.raise_for_status()
            cr_json = cr.json()
            rows = cr_json.get("results") or []
            has_next = bool(cr_json.get("more"))

        new_rows: list[dict] = []
        broke_mid_page = False
        for ch_data in rows:
            ext_map = db.scalar(
                select(ExternalMapping).where(
                    ExternalMapping.entity_type == "character",
                    ExternalMapping.provider == "vndb",
                    ExternalMapping.external_id == ch_data["id"],
                )
            )
            if ext_map and ext_map.entity_id in existing_char_ids:
                continue
            new_rows.append(ch_data)
            if len(new_rows) >= count:
                broke_mid_page = True
                break

        sem = asyncio.Semaphore(14)

        async def _res_vndb_char(ch_data: dict) -> tuple[str, str]:
            cname = ch_data.get("name") or "Unknown"
            cbio = clean_html_markup(ch_data.get("description") or "")
            if locale != "ru":
                return cname, cbio
            async with sem:
                n_coro = translate_text(cname, "ru")
                b_coro = translate_text(cbio[:900], "ru") if cbio else asyncio.sleep(0, result=("", "api_raw"))
                (rn, _), (rb, _) = await asyncio.gather(n_coro, b_coro)
                return (rn or cname), rb

        resolved_vndb = await asyncio.gather(*[_res_vndb_char(r) for r in new_rows])
        added = []
        for ch_data, (ru_name, ru_bio) in zip(new_rows, resolved_vndb):
            cname = ch_data.get("name") or "Unknown"
            sex_raw = ch_data.get("sex")
            gender = {"m": "male", "f": "female"}.get(sex_raw[0], "unknown") if sex_raw else "unknown"
            ch = await upsert_character_from_external(
                db,
                provider="vndb",
                external_id=ch_data["id"],
                external_url=f"https://vndb.org/{ch_data['id']}",
                canonical_name=cname,
                native_name=ch_data.get("original") or "",
                gender=gender,
                image_url=(ch_data.get("image") or {}).get("url") or "",
                bio_en=ch_data.get("description") or "",
                name_ru=ru_name if locale == "ru" else cname,
                bio_ru=ru_bio if locale == "ru" else "",
                translate_bio=False,
                target_locale=locale,
                media_id=media.id,
                role="supporting",
            )
            added.append({"id": ch.id, "slug": ch.slug, "name": ru_name if locale == "ru" else cname})
        has_more_out = bool(broke_mid_page or has_next)
        if not has_more_out:
            media.characters_exhausted = True
        db.commit()
        next_vndb_page = page if broke_mid_page else (page + 1 if has_next else page)
        return {
            "media_id": media.id,
            "added_count": len(added),
            "next_page": next_vndb_page,
            "has_more": has_more_out,
            "media_exhausted": bool(media.characters_exhausted),
            "characters": added,
        }

    raise ValueError(f"Per-title character batch import is currently supported for AniList and VNDB (got {mapping.provider}).")
