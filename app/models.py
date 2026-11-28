from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Franchise(Base):
    __tablename__ = "franchises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(200), index=True)
    description_ru: Mapped[str] = mapped_column(Text, default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    media_items: Mapped[list["Media"]] = relationship(back_populates="franchise")
    characters: Mapped[list["Character"]] = relationship(back_populates="franchise")


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    franchise_id: Mapped[int | None] = mapped_column(ForeignKey("franchises.id"), nullable=True, index=True)
    media_type: Mapped[str] = mapped_column(String(32), index=True)  # anime, manga, manhwa, manhua, novel, game, vn, series
    canonical_title: Mapped[str] = mapped_column(Text, index=True)
    native_title: Mapped[str] = mapped_column(Text, default="")
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    rating: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    popularity: Mapped[int] = mapped_column(Integer, default=0, index=True)
    cover_url: Mapped[str] = mapped_column(Text, default="")
    banner_url: Mapped[str] = mapped_column(Text, default="")
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_label: Mapped[str] = mapped_column(String(16), default="ep")  # ep, ch, hr
    characters_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    franchise: Mapped["Franchise | None"] = relationship(back_populates="media_items")
    translations: Mapped[list["MediaTranslation"]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )
    character_links: Mapped[list["MediaCharacter"]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )


class MediaTranslation(Base):
    __tablename__ = "media_translations"
    __table_args__ = (UniqueConstraint("media_id", "locale", name="uq_media_locale"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    locale: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(Text)
    synopsis: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="api_raw")  # api_raw | ai_draft | community_verified
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    media: Mapped["Media"] = relationship(back_populates="translations")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    franchise_id: Mapped[int | None] = mapped_column(ForeignKey("franchises.id"), nullable=True, index=True)
    canonical_name: Mapped[str] = mapped_column(Text, index=True)
    native_name: Mapped[str] = mapped_column(Text, default="")
    gender: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    hair_color: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    eye_color: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    hair_length: Mapped[str] = mapped_column(String(40), default="unknown")
    mbti: Mapped[str] = mapped_column(String(8), default="", index=True)
    enneagram: Mapped[str] = mapped_column(String(16), default="")
    archetype: Mapped[str] = mapped_column(String(80), default="")
    birthday: Mapped[str] = mapped_column(String(40), default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    popularity: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    franchise: Mapped["Franchise | None"] = relationship(back_populates="characters")
    translations: Mapped[list["CharacterTranslation"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    media_links: Mapped[list["MediaCharacter"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    trait_links: Mapped[list["CharacterTrait"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class CharacterTranslation(Base):
    __tablename__ = "character_translations"
    __table_args__ = (UniqueConstraint("character_id", "locale", name="uq_character_locale"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    locale: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(Text)
    bio: Mapped[str] = mapped_column(Text, default="")
    appearance_notes: Mapped[str] = mapped_column(Text, default="")
    abilities_summary: Mapped[str] = mapped_column(Text, default="")
    spoiler_bio: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="api_raw")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    character: Mapped["Character"] = relationship(back_populates="translations")


class MediaCharacter(Base):
    __tablename__ = "media_characters"
    __table_args__ = (UniqueConstraint("media_id", "character_id", name="uq_media_character"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="supporting", index=True)  # main | supporting | antagonist
    voice_actor: Mapped[str] = mapped_column(Text, default="")
    voice_actor_lang: Mapped[str] = mapped_column(String(16), default="Japanese")
    local_image_url: Mapped[str] = mapped_column(Text, default="")
    spoiler_after_unit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    media: Mapped["Media"] = relationship(back_populates="character_links")
    character: Mapped["Character"] = relationship(back_populates="media_links")


class Trait(Base):
    __tablename__ = "traits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)  # appearance | personality | ability
    name_en: Mapped[str] = mapped_column(String(100))
    name_ru: Mapped[str] = mapped_column(String(100))


class CharacterTrait(Base):
    __tablename__ = "character_traits"
    __table_args__ = (UniqueConstraint("character_id", "trait_id", name="uq_character_trait"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    trait_id: Mapped[int] = mapped_column(ForeignKey("traits.id", ondelete="CASCADE"), index=True)
    votes_count: Mapped[int] = mapped_column(Integer, default=1)

    character: Mapped["Character"] = relationship(back_populates="trait_links")
    trait: Mapped["Trait"] = relationship()


class TraitVote(Base):
    __tablename__ = "trait_votes"
    __table_args__ = (
        UniqueConstraint("character_id", "trait_id", "voter_key", name="uq_trait_vote_voter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    trait_id: Mapped[int] = mapped_column(ForeignKey("traits.id", ondelete="CASCADE"), index=True)
    voter_key: Mapped[str] = mapped_column(String(80), index=True)


class ExternalMapping(Base):
    __tablename__ = "external_mappings"
    __table_args__ = (
        UniqueConstraint("entity_type", "provider", "external_id", name="uq_external_mapping"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)  # media | character | franchise
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # anilist | igdb | vndb | shikimori | wikidata
    external_id: Mapped[str] = mapped_column(String(120))
    external_url: Mapped[str] = mapped_column(String(500), default="")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)  # media | character
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    locale: Mapped[str] = mapped_column(String(10), default="ru")
    author_name: Mapped[str] = mapped_column(String(100), default="Community Contributor")
    field_name: Mapped[str] = mapped_column(String(64))  # title, synopsis, bio, abilities_summary, spoiler_bio, mbti, hair_color...
    previous_value: Mapped[str] = mapped_column(Text, default="")
    proposed_value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserListEntry(Base):
    __tablename__ = "user_list_entries"
    __table_args__ = (UniqueConstraint("profile_key", "media_id", name="uq_profile_media"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_key: Mapped[str] = mapped_column(String(80), default="guest", index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="planned")  # active | completed | planned | dropped
    progress_unit: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    media: Mapped["Media"] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(24), default="user")  # user | moderator | admin
    is_moderator: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    preferred_locale: Mapped[str] = mapped_column(String(10), default="ru")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    custom_role_links: Mapped[list["UserCustomRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class CustomRole(Base):
    __tablename__ = "custom_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    badge_color: Mapped[str] = mapped_column(String(24), default="#7c6bff")
    nick_color: Mapped[str] = mapped_column(String(24), default="#eef2f9")
    avatar_frame: Mapped[str] = mapped_column(String(32), default="solid")  # none | solid | glow | rainbow
    frame_color: Mapped[str] = mapped_column(String(24), default="#7c6bff")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserCustomRole(Base):
    __tablename__ = "user_custom_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_custom_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("custom_roles.id", ondelete="CASCADE"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="custom_role_links")
    role: Mapped["CustomRole"] = relationship()


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    __table_args__ = (UniqueConstraint("provider", "category", name="uq_sync_provider_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    next_page: Mapped[int] = mapped_column(Integer, default=1)
    total_imported: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
