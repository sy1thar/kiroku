from datetime import datetime, timezone
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.i18n import normalize_for_search, normalize_unicode_raw
from app.models import (
    Character,
    CharacterTrait,
    CharacterTranslation,
    ExternalMapping,
    Franchise,
    Media,
    MediaCharacter,
    MediaTranslation,
    Revision,
    Trait,
    TraitVote,
    UserListEntry,
)


def pick_media_translation(media: Media, locale: str) -> MediaTranslation | None:
    return next((t for t in media.translations if t.locale == locale), None) or (
        media.translations[0] if media.translations else None
    )


def pick_char_translation(character: Character, locale: str) -> CharacterTranslation | None:
    return next((t for t in character.translations if t.locale == locale), None) or (
        character.translations[0] if character.translations else None
    )


def _matches_multilingual(query: str, candidates: list[str]) -> bool:
    q_raw = query.strip().lower()
    q_uni = normalize_unicode_raw(query)
    q_norm = normalize_for_search(query)
    for cand in candidates:
        if not cand:
            continue
        if q_raw in cand.lower():
            return True
        if q_uni and q_uni in normalize_unicode_raw(cand):
            return True
        if q_norm and q_norm in normalize_for_search(cand):
            return True
    return False


def list_media(
    db: Session,
    *,
    query: str | None = None,
    media_type: str | None = None,
    franchise_slug: str | None = None,
    genre: str | None = None,
    sort: str = "popularity",
    limit: int = 60,
) -> list[Media]:
    stmt = select(Media).options(
        selectinload(Media.translations),
        selectinload(Media.franchise),
    )
    if media_type:
        stmt = stmt.where(Media.media_type == media_type)
    if franchise_slug:
        stmt = stmt.join(Franchise).where(Franchise.slug == franchise_slug)

    if sort == "rating":
        stmt = stmt.order_by(desc(Media.rating), desc(Media.popularity))
    elif sort == "newest":
        stmt = stmt.order_by(desc(Media.release_year), desc(Media.popularity))
    else:
        stmt = stmt.order_by(desc(Media.popularity), desc(Media.rating))

    rows = list(db.scalars(stmt.limit(limit * 3)).all())
    if query and query.strip():
        rows = [
            m
            for m in rows
            if _matches_multilingual(
                query,
                [m.canonical_title, m.native_title]
                + ([m.franchise.canonical_name] if m.franchise else [])
                + [t.title for t in m.translations],
            )
        ]
    if genre:
        g_low = genre.lower()
        rows = [m for m in rows if any(g.lower() == g_low for g in (m.genres or []))]
    return rows[:limit]


def get_media_detail(db: Session, slug: str, profile_key: str = "guest") -> dict | None:
    media = db.scalar(
        select(Media)
        .where(Media.slug == slug)
        .options(
            selectinload(Media.translations),
            selectinload(Media.franchise).selectinload(Franchise.media_items).selectinload(Media.translations),
            selectinload(Media.character_links)
            .selectinload(MediaCharacter.character)
            .selectinload(Character.translations),
        )
    )
    if not media:
        return None

    mappings = list(
        db.scalars(
            select(ExternalMapping).where(
                ExternalMapping.entity_type == "media",
                ExternalMapping.entity_id == media.id,
            )
        ).all()
    )
    tracker = db.scalar(
        select(UserListEntry).where(
            UserListEntry.profile_key == profile_key,
            UserListEntry.media_id == media.id,
        )
    )
    revisions = list(
        db.scalars(
            select(Revision)
            .where(Revision.entity_type == "media", Revision.entity_id == media.id)
            .order_by(desc(Revision.created_at))
            .limit(10)
        ).all()
    )
    return {
        "media": media,
        "mappings": mappings,
        "tracker": tracker,
        "revisions": revisions,
    }


def list_characters(
    db: Session,
    *,
    query: str | None = None,
    media_type: str | None = None,
    gender: str | None = None,
    hair_color: str | None = None,
    eye_color: str | None = None,
    mbti: str | None = None,
    trait_slug: str | None = None,
    limit: int = 60,
) -> list[Character]:
    stmt = (
        select(Character)
        .options(
            selectinload(Character.translations),
            selectinload(Character.franchise),
            selectinload(Character.trait_links).selectinload(CharacterTrait.trait),
            selectinload(Character.media_links).selectinload(MediaCharacter.media),
        )
        .order_by(desc(Character.popularity))
    )
    if gender:
        stmt = stmt.where(Character.gender == gender)
    if hair_color:
        stmt = stmt.where(Character.hair_color == hair_color)
    if eye_color:
        stmt = stmt.where(Character.eye_color == eye_color)
    if mbti:
        stmt = stmt.where(Character.mbti == mbti.upper())

    rows = list(db.scalars(stmt).all())
    if query and query.strip():
        rows = [
            c
            for c in rows
            if _matches_multilingual(
                query,
                [c.canonical_name, c.native_name, c.archetype, c.mbti]
                + ([c.franchise.canonical_name] if c.franchise else [])
                + [t.name for t in c.translations],
            )
        ]
    if media_type:
        rows = [
            c
            for c in rows
            if any(link.media and link.media.media_type == media_type for link in c.media_links)
        ]
    if trait_slug:
        rows = [
            c
            for c in rows
            if any(tl.trait and tl.trait.slug == trait_slug for tl in c.trait_links)
        ]
    return rows[:limit]


def get_character_detail(db: Session, slug: str) -> dict | None:
    character = db.scalar(
        select(Character)
        .where(Character.slug == slug)
        .options(
            selectinload(Character.translations),
            selectinload(Character.franchise),
            selectinload(Character.trait_links).selectinload(CharacterTrait.trait),
            selectinload(Character.media_links)
            .selectinload(MediaCharacter.media)
            .selectinload(Media.translations),
        )
    )
    if not character:
        return None

    mappings = list(
        db.scalars(
            select(ExternalMapping).where(
                ExternalMapping.entity_type == "character",
                ExternalMapping.entity_id == character.id,
            )
        ).all()
    )
    all_traits = list(db.scalars(select(Trait).order_by(Trait.category, Trait.name_en)).all())
    revisions = list(
        db.scalars(
            select(Revision)
            .where(Revision.entity_type == "character", Revision.entity_id == character.id)
            .order_by(desc(Revision.created_at))
            .limit(10)
        ).all()
    )
    return {
        "character": character,
        "mappings": mappings,
        "all_traits": all_traits,
        "revisions": revisions,
    }


def get_voted_trait_slugs(db: Session, character_id: int, voter_key: str) -> set[str]:
    if not voter_key:
        return set()
    rows = db.scalars(
        select(Trait.slug)
        .join(TraitVote, TraitVote.trait_id == Trait.id)
        .where(
            TraitVote.character_id == character_id,
            TraitVote.voter_key == voter_key,
        )
    ).all()
    return set(rows)


def vote_character_trait(db: Session, character_id: int, trait_slug: str, voter_key: str) -> tuple[int, bool]:
    """Toggle one vote per voter_key on a character trait. Returns (votes_count, is_voted)."""
    trait = db.scalar(select(Trait).where(Trait.slug == trait_slug))
    if not trait:
        raise ValueError(f"Unknown trait: {trait_slug}")

    link = db.scalar(
        select(CharacterTrait).where(
            CharacterTrait.character_id == character_id,
            CharacterTrait.trait_id == trait.id,
        )
    )
    existing_vote = db.scalar(
        select(TraitVote).where(
            TraitVote.character_id == character_id,
            TraitVote.trait_id == trait.id,
            TraitVote.voter_key == voter_key,
        )
    )
    if existing_vote:
        db.delete(existing_vote)
        if link and link.votes_count > 0:
            link.votes_count -= 1
        db.commit()
        return (link.votes_count if link else 0), False

    db.add(TraitVote(character_id=character_id, trait_id=trait.id, voter_key=voter_key))
    if link:
        link.votes_count += 1
    else:
        link = CharacterTrait(character_id=character_id, trait_id=trait.id, votes_count=1)
        db.add(link)
    db.commit()
    return link.votes_count, True


def create_revision(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    locale: str,
    author_name: str,
    field_name: str,
    proposed_value: str,
    reason: str,
) -> Revision:
    prev_value = ""
    if entity_type == "media":
        trans = db.scalar(
            select(MediaTranslation).where(
                MediaTranslation.media_id == entity_id,
                MediaTranslation.locale == locale,
            )
        )
        if trans and hasattr(trans, field_name):
            prev_value = str(getattr(trans, field_name) or "")
    elif entity_type == "character":
        char = db.get(Character, entity_id)
        trans = db.scalar(
            select(CharacterTranslation).where(
                CharacterTranslation.character_id == entity_id,
                CharacterTranslation.locale == locale,
            )
        )
        if field_name in ("mbti", "enneagram", "hair_color", "eye_color", "archetype") and char:
            prev_value = str(getattr(char, field_name) or "")
        elif trans and hasattr(trans, field_name):
            prev_value = str(getattr(trans, field_name) or "")

    rev = Revision(
        entity_type=entity_type,
        entity_id=entity_id,
        locale=locale,
        author_name=author_name.strip() or "Community Contributor",
        field_name=field_name,
        previous_value=prev_value,
        proposed_value=proposed_value.strip(),
        reason=reason.strip(),
        status="pending",
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)
    return rev


def decide_revision(db: Session, revision_id: int, action: str) -> Revision:
    rev = db.get(Revision, revision_id)
    if not rev:
        raise ValueError("Revision not found")
    if rev.status != "pending":
        return rev

    if action == "approve":
        if rev.entity_type == "media":
            trans = db.scalar(
                select(MediaTranslation).where(
                    MediaTranslation.media_id == rev.entity_id,
                    MediaTranslation.locale == rev.locale,
                )
            )
            if not trans:
                media = db.get(Media, rev.entity_id)
                trans = MediaTranslation(
                    media_id=rev.entity_id,
                    locale=rev.locale,
                    title=media.canonical_title if media else "Title",
                )
                db.add(trans)
            if rev.field_name in ("title", "synopsis"):
                setattr(trans, rev.field_name, rev.proposed_value)
                trans.source = "community_verified"
        elif rev.entity_type == "character":
            char = db.get(Character, rev.entity_id)
            if rev.field_name in ("mbti", "enneagram", "hair_color", "eye_color", "archetype") and char:
                setattr(char, rev.field_name, rev.proposed_value)
            else:
                trans = db.scalar(
                    select(CharacterTranslation).where(
                        CharacterTranslation.character_id == rev.entity_id,
                        CharacterTranslation.locale == rev.locale,
                    )
                )
                if not trans:
                    trans = CharacterTranslation(
                        character_id=rev.entity_id,
                        locale=rev.locale,
                        name=char.canonical_name if char else "Character",
                    )
                    db.add(trans)
                if rev.field_name in ("name", "bio", "appearance_notes", "abilities_summary", "spoiler_bio"):
                    setattr(trans, rev.field_name, rev.proposed_value)
                    trans.source = "community_verified"
        rev.status = "approved"
    else:
        rev.status = "rejected"

    rev.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rev)
    return rev


def upsert_user_list(
    db: Session,
    *,
    media_id: int,
    status: str,
    progress_unit: int,
    score: int | None,
    is_favorite: bool,
    notes: str = "",
    profile_key: str = "guest",
) -> UserListEntry:
    entry = db.scalar(
        select(UserListEntry).where(
            UserListEntry.profile_key == profile_key,
            UserListEntry.media_id == media_id,
        )
    )
    if not entry:
        entry = UserListEntry(
            profile_key=profile_key,
            media_id=media_id,
            status=status,
            progress_unit=progress_unit,
            score=score,
            is_favorite=is_favorite,
            notes=notes,
        )
        db.add(entry)
    else:
        entry.status = status
        entry.progress_unit = progress_unit
        entry.score = score
        entry.is_favorite = is_favorite
        entry.notes = notes
    db.commit()
    db.refresh(entry)
    return entry
