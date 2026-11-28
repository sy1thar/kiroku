import re
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import slugify
from app.models import (
    Character,
    CharacterTranslation,
    ExternalMapping,
    Media,
    MediaCharacter,
    MediaTranslation,
)
from app.services.translation import fetch_shikimori_character_ru, translate_text


_HAIR_PATTERNS: list[tuple[str, str]] = [
    (r"\b(white|silver|grey|gray)[- ]haired|\b(white|silver|grey|gray) hair\b", "white"),
    (r"\b(black|dark|raven)[- ]haired|\b(black|raven) hair\b", "black"),
    (r"\b(blonde|blond|golden)[- ]haired|\b(blonde|blond|golden) hair\b", "blonde"),
    (r"\b(red|crimson|scarlet|orange|ginger)[- ]haired|\b(red|crimson|scarlet|orange|ginger) hair\b", "red"),
    (r"\b(blue|cyan|teal)[- ]haired|\b(blue|cyan|teal) hair\b", "blue"),
    (r"\b(green|emerald)[- ]haired|\b(green|emerald) hair\b", "green"),
    (r"\b(pink|rose)[- ]haired|\b(pink|rose) hair\b", "pink"),
    (r"\b(purple|violet|lavender)[- ]haired|\b(purple|violet|lavender) hair\b", "purple"),
    (r"\b(brown|brunette|chestnut)[- ]haired|\b(brown|chestnut) hair\b", "brown"),
]

_EYE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(blue|azure|sapphire) eyes\b", "blue"),
    (r"\b(red|crimson|scarlet) eyes\b", "red"),
    (r"\b(green|emerald) eyes\b", "green"),
    (r"\b(amber|gold|golden|yellow) eyes\b", "amber"),
    (r"\b(brown|hazel|dark) eyes\b", "brown"),
    (r"\b(purple|violet) eyes\b", "purple"),
    (r"\b(black|obsidian) eyes\b", "black"),
    (r"\b(grey|gray|silver) eyes\b", "grey"),
    (r"\b(pink) eyes\b", "pink"),
]


def infer_visual_colors(bio: str) -> tuple[str, str]:
    """Infer hair and eye colors from English character biography text."""
    low = (bio or "").lower()
    hair = "unknown"
    eyes = "unknown"
    for pat, color in _HAIR_PATTERNS:
        if re.search(pat, low):
            hair = color
            break
    for pat, color in _EYE_PATTERNS:
        if re.search(pat, low):
            eyes = color
            break
    return hair, eyes


def clean_html_markup(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Convert AniList markdown links [Name](url) -> Name and __label__ -> label
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    # Strip AniList spoiler markers ~!...!~ into readable brackets
    text = re.sub(r"~!(.+?)!~", r"[\1]", text, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def normalize_gender(raw: str | None) -> str:
    """Normalize verbose upstream gender descriptions (e.g. AniList free-text) into canonical keys."""
    s = (raw or "").strip().lower()
    if not s or s == "unknown":
        return "unknown"
    if s.startswith("female") or s == "f":
        return "female"
    if s.startswith("male") or s == "m":
        return "male"
    if any(w in s for w in ("female", "woman", "girl")):
        return "female"
    if any(w in s for w in ("male", "man", "boy")):
        return "male"
    if any(w in s for w in ("non", "other", "agender", "genderless", "herm")):
        return "other"
    return s[:30]


def _unique_slug(db: Session, model_cls, base_slug: str) -> str:
    clipped = (base_slug or "entry")[:120].strip("-") or "entry"
    candidate = clipped
    idx = 2
    while db.scalar(select(model_cls.id).where(model_cls.slug == candidate)) is not None:
        candidate = f"{clipped}-{idx}"
        idx += 1
    return candidate


async def upsert_media_from_external(
    db: Session,
    *,
    provider: str,
    external_id: str,
    external_url: str,
    media_type: str,
    canonical_title: str,
    native_title: str = "",
    release_year: int | None = None,
    status: str = "completed",
    rating: float = 0.0,
    popularity: int = 0,
    cover_url: str = "",
    banner_url: str = "",
    total_units: int | None = None,
    unit_label: str = "ep",
    genres: list[str] | None = None,
    platforms: list[str] | None = None,
    synopsis_en: str = "",
    title_ru: str = "",
    synopsis_ru: str = "",
    target_locale: str = "ru",
) -> Media:
    mapping = db.scalar(
        select(ExternalMapping).where(
            ExternalMapping.entity_type == "media",
            ExternalMapping.provider == provider,
            ExternalMapping.external_id == str(external_id),
        )
    )

    media: Media | None = None
    if mapping:
        media = db.get(Media, mapping.entity_id)

    if not media:
        slug = _unique_slug(db, Media, slugify(canonical_title))
        media = Media(
            slug=slug,
            media_type=media_type,
            canonical_title=canonical_title,
            native_title=native_title,
            release_year=release_year,
            status=status,
            rating=rating,
            popularity=popularity,
            cover_url=cover_url,
            banner_url=banner_url,
            total_units=total_units,
            unit_label=unit_label,
            genres=genres or [],
            platforms=platforms or [],
        )
        db.add(media)
        db.flush()

        db.add(
            ExternalMapping(
                entity_type="media",
                entity_id=media.id,
                provider=provider,
                external_id=str(external_id),
                external_url=external_url,
            )
        )
    else:
        media.rating = rating or media.rating
        media.popularity = popularity or media.popularity
        if cover_url:
            media.cover_url = cover_url
        if banner_url:
            media.banner_url = banner_url
        if genres:
            media.genres = genres
        if mapping:
            mapping.last_synced_at = datetime.now(timezone.utc)

    clean_en = clean_html_markup(synopsis_en)
    _ensure_media_translation(db, media.id, "en", canonical_title, clean_en, "api_raw")

    if target_locale == "ru":
        resolved_title_ru = title_ru.strip()
        if not resolved_title_ru:
            resolved_title_ru, _ = await translate_text(canonical_title, "ru")

        ru_synopsis = synopsis_ru.strip()
        ru_source = "community_verified" if ru_synopsis else "ai_draft"
        if not ru_synopsis and clean_en:
            ru_synopsis, ru_source = await translate_text(clean_en, "ru")

        _ensure_media_translation(
            db,
            media.id,
            "ru",
            resolved_title_ru or canonical_title,
            ru_synopsis,
            ru_source,
        )
    else:
        _ensure_media_translation(
            db,
            media.id,
            "ru",
            title_ru.strip() or canonical_title,
            synopsis_ru.strip(),
            "api_raw",
        )
    db.flush()
    return media


def _ensure_media_translation(
    db: Session,
    media_id: int,
    locale: str,
    title: str,
    synopsis: str,
    source: str,
) -> None:
    existing = db.scalar(
        select(MediaTranslation).where(
            MediaTranslation.media_id == media_id,
            MediaTranslation.locale == locale,
        )
    )
    if not existing:
        db.add(
            MediaTranslation(
                media_id=media_id,
                locale=locale,
                title=title,
                synopsis=synopsis,
                source=source,
            )
        )
    elif existing.source != "community_verified" and (synopsis or title):
        if title and existing.title != title:
            existing.title = title
        if synopsis and not existing.synopsis:
            existing.synopsis = synopsis
        existing.source = source


async def upsert_character_from_external(
    db: Session,
    *,
    provider: str,
    external_id: str,
    external_url: str,
    canonical_name: str,
    native_name: str = "",
    gender: str = "unknown",
    hair_color: str = "unknown",
    eye_color: str = "unknown",
    image_url: str = "",
    bio_en: str = "",
    name_ru: str = "",
    bio_ru: str = "",
    translate_bio: bool = True,
    target_locale: str = "ru",
    popularity: int = 0,
    media_id: int | None = None,
    role: str = "supporting",
    voice_actor: str = "",
) -> Character:
    mapping = db.scalar(
        select(ExternalMapping).where(
            ExternalMapping.entity_type == "character",
            ExternalMapping.provider == provider,
            ExternalMapping.external_id == str(external_id),
        )
    )
    character: Character | None = db.get(Character, mapping.entity_id) if mapping else None

    clean_gender = normalize_gender(gender)
    clean_hair = (hair_color or "unknown").strip().lower()[:38]
    clean_eye = (eye_color or "unknown").strip().lower()[:38]
    clean_role = (role or "supporting").strip().lower()[:30]

    if not character:
        slug = _unique_slug(db, Character, slugify(canonical_name))
        character = Character(
            slug=slug,
            canonical_name=canonical_name,
            native_name=native_name,
            gender=clean_gender,
            hair_color=clean_hair,
            eye_color=clean_eye,
            image_url=image_url,
            popularity=popularity,
        )
        db.add(character)
        db.flush()
        db.add(
            ExternalMapping(
                entity_type="character",
                entity_id=character.id,
                provider=provider,
                external_id=str(external_id),
                external_url=external_url,
            )
        )
    else:
        if image_url and not character.image_url:
            character.image_url = image_url
        if popularity > character.popularity:
            character.popularity = popularity

    clean_bio = clean_html_markup(bio_en)
    if hair_color == "unknown" or eye_color == "unknown":
        inferred_hair, inferred_eyes = infer_visual_colors(clean_bio)
        if character.hair_color == "unknown" and inferred_hair != "unknown":
            character.hair_color = inferred_hair
        if character.eye_color == "unknown" and inferred_eyes != "unknown":
            character.eye_color = inferred_eyes

    _ensure_char_translation(db, character.id, "en", canonical_name, clean_bio, "api_raw")

    if target_locale == "ru":
        resolved_name_ru = name_ru.strip()
        if not resolved_name_ru:
            resolved_name_ru = await fetch_shikimori_character_ru(canonical_name)
        if not resolved_name_ru:
            resolved_name_ru, _ = await translate_text(canonical_name, "ru")

        if bio_ru.strip():
            ru_bio, ru_source = bio_ru.strip(), "ai_draft"
        elif translate_bio and clean_bio:
            ru_bio, ru_source = await translate_text(clean_bio, "ru")
        else:
            ru_bio, ru_source = clean_bio, "api_raw"
        _ensure_char_translation(
            db,
            character.id,
            "ru",
            resolved_name_ru or canonical_name,
            ru_bio,
            ru_source,
        )
    else:
        _ensure_char_translation(
            db,
            character.id,
            "ru",
            name_ru.strip() or canonical_name,
            bio_ru.strip(),
            "api_raw",
        )

    if media_id is not None:
        link = db.scalar(
            select(MediaCharacter).where(
                MediaCharacter.media_id == media_id,
                MediaCharacter.character_id == character.id,
            )
        )
        if not link:
            db.add(
                MediaCharacter(
                    media_id=media_id,
                    character_id=character.id,
                    role=clean_role,
                    voice_actor=(voice_actor or "").strip(),
                    local_image_url=image_url,
                )
            )
    db.flush()
    return character


def _ensure_char_translation(
    db: Session,
    character_id: int,
    locale: str,
    name: str,
    bio: str,
    source: str,
) -> None:
    existing = db.scalar(
        select(CharacterTranslation).where(
            CharacterTranslation.character_id == character_id,
            CharacterTranslation.locale == locale,
        )
    )
    if not existing:
        db.add(
            CharacterTranslation(
                character_id=character_id,
                locale=locale,
                name=name,
                bio=bio,
                source=source,
            )
        )
    elif existing.source != "community_verified" and (bio or name):
        if name and existing.name != name:
            existing.name = name
        if bio and not existing.bio:
            existing.bio = bio
        existing.source = source
