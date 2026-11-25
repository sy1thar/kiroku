from difflib import SequenceMatcher
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.i18n import normalize_for_search, normalize_unicode_raw
from app.models import Character, Franchise, Media


def _score_match(query_raw: str, query_unicode: str, query_norm: str, candidates: list[str]) -> float:
    best = 0.0
    q_low = query_raw.lower().strip()
    q_compact = query_norm.replace(" ", "")
    for text in candidates:
        if not text:
            continue
        low = text.lower()
        if q_low == low:
            return 1.0
        if q_low in low:
            best = max(best, 0.94)

        u_norm = normalize_unicode_raw(text)
        if query_unicode and u_norm:
            if query_unicode == u_norm:
                return 0.99
            if query_unicode in u_norm:
                best = max(best, 0.92)

        norm = normalize_for_search(text)
        if not norm or not query_norm:
            continue
        if query_norm == norm:
            return 0.98
        if query_norm in norm or (q_compact and q_compact in norm.replace(" ", "")):
            best = max(best, 0.89)
        else:
            ratio = SequenceMatcher(None, q_compact or query_norm, norm.replace(" ", "")).ratio()
            if ratio >= 0.56:
                best = max(best, ratio * 0.84)
    return best


def global_search(
    db: Session,
    query: str,
    locale: str = "ru",
    media_type: str | None = None,
    limit: int = 12,
) -> dict:
    """
    Universal cross-language search across Media, Characters, and Franchises.
    Matches Russian, English, Japanese, Korean, and romanized queries regardless of UI locale.
    """
    q = query.strip()
    if not q:
        return {"query": "", "engine": "empty", "media": [], "characters": [], "franchises": []}

    settings = get_settings()
    if settings.meilisearch_url:
        try:
            import meilisearch  # type: ignore

            client = meilisearch.Client(settings.meilisearch_url, settings.meilisearch_api_key or None)
            media_filter = f'media_type = "{media_type}"' if media_type else None
            m_res = client.index("media").search(q, {"limit": limit, "filter": media_filter})
            c_res = client.index("characters").search(q, {"limit": limit})
            return {
                "query": q,
                "engine": "meilisearch",
                "media": m_res.get("hits", []),
                "characters": c_res.get("hits", []),
                "franchises": [],
            }
        except Exception:
            pass

    q_unicode = normalize_unicode_raw(q)
    q_norm = normalize_for_search(q)

    media_stmt = select(Media).options(
        selectinload(Media.translations),
        selectinload(Media.franchise),
    )
    if media_type:
        media_stmt = media_stmt.where(Media.media_type == media_type)
    media_rows = db.scalars(media_stmt).all()

    scored_media = []
    for m in media_rows:
        trans = next((t for t in m.translations if t.locale == locale), None) or (
            m.translations[0] if m.translations else None
        )
        candidates = (
            [m.canonical_title, m.native_title]
            + ([m.franchise.canonical_name] if m.franchise else [])
            + (m.genres or [])
            + [t.title for t in m.translations]
        )
        score = _score_match(q, q_unicode, q_norm, candidates)
        if score > 0.45:
            scored_media.append(
                (
                    score,
                    {
                        "id": m.id,
                        "slug": m.slug,
                        "media_type": m.media_type,
                        "title": trans.title if trans else m.canonical_title,
                        "canonical_title": m.canonical_title,
                        "release_year": m.release_year,
                        "rating": m.rating,
                        "cover_url": m.cover_url,
                        "score": round(score, 3),
                    },
                )
            )
    scored_media.sort(key=lambda item: (-item[0], -item[1]["rating"]))

    char_rows = db.scalars(
        select(Character).options(
            selectinload(Character.translations),
            selectinload(Character.franchise),
        )
    ).all()
    scored_chars = []
    for c in char_rows:
        trans = next((t for t in c.translations if t.locale == locale), None) or (
            c.translations[0] if c.translations else None
        )
        candidates = (
            [c.canonical_name, c.native_name, c.archetype, c.mbti]
            + ([c.franchise.canonical_name] if c.franchise else [])
            + [t.name for t in c.translations]
        )
        score = _score_match(q, q_unicode, q_norm, candidates)
        if score > 0.45:
            scored_chars.append(
                (
                    score,
                    {
                        "id": c.id,
                        "slug": c.slug,
                        "name": trans.name if trans else c.canonical_name,
                        "canonical_name": c.canonical_name,
                        "hair_color": c.hair_color,
                        "eye_color": c.eye_color,
                        "mbti": c.mbti,
                        "image_url": c.image_url,
                        "popularity": c.popularity,
                        "score": round(score, 3),
                    },
                )
            )
    scored_chars.sort(key=lambda item: (-item[0], -item[1]["popularity"]))

    fran_rows = db.scalars(select(Franchise)).all()
    scored_franchises = []
    for f in fran_rows:
        score = _score_match(q, q_unicode, q_norm, [f.canonical_name, f.slug])
        if score > 0.5:
            scored_franchises.append(
                (
                    score,
                    {
                        "id": f.id,
                        "slug": f.slug,
                        "name": f.canonical_name,
                        "description": f.description_ru if locale == "ru" else f.description_en,
                    },
                )
            )
    scored_franchises.sort(key=lambda item: -item[0])

    return {
        "query": q,
        "engine": "kiroku-cross-script",
        "media": [x[1] for x in scored_media[:limit]],
        "characters": [x[1] for x in scored_chars[:limit]],
        "franchises": [x[1] for x in scored_franchises[:5]],
    }
