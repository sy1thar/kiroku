from pydantic import BaseModel, Field


class TraitVoteRequest(BaseModel):
    character_id: int
    trait_slug: str


class RevisionCreateRequest(BaseModel):
    entity_type: str = Field(pattern="^(media|character)$")
    entity_id: int
    locale: str = "ru"
    author_name: str = "Community Contributor"
    field_name: str
    proposed_value: str = Field(min_length=1)
    reason: str = ""


class RevisionDecisionRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


class UserListUpsertRequest(BaseModel):
    media_id: int
    status: str = Field(pattern="^(active|completed|planned|dropped)$")
    progress_unit: int = Field(ge=0, default=0)
    score: int | None = Field(ge=1, le=10, default=None)
    is_favorite: bool = False
    notes: str = ""


class ImportQueryRequest(BaseModel):
    provider: str = Field(pattern="^(anilist|vndb|igdb)$")
    query: str = Field(min_length=2)
    media_type: str | None = None
    limit: int = Field(ge=1, le=15, default=5)


class BatchImportRequest(BaseModel):
    category: str = Field(pattern="^(anime|manga|manhwa|manhua|novel|game|vn|series)$")
    count: int = Field(ge=3, le=15, default=10)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern="^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    login: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=6, max_length=128)


class AccountProfileUpdateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern="^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=160)
    avatar_url: str = Field(max_length=500_000, default="")
    bio: str = Field(max_length=1000, default="")
    preferred_locale: str = Field(pattern="^(ru|en)$", default="ru")


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class AdminRoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(user|moderator|admin)$")


class AdminUsernameRoleRequest(BaseModel):
    username: str = Field(min_length=2, max_length=60)
    role: str = Field(pattern="^(user|moderator|admin)$")


class AdminImportCharactersRequest(BaseModel):
    count: int = Field(ge=1, le=20, default=12)


class AdminMediaUpdateRequest(BaseModel):
    locale: str = Field(pattern="^(ru|en)$", default="ru")
    title: str = Field(min_length=1, max_length=255)
    synopsis: str = ""
    cover_url: str = ""
    rating: float = Field(ge=0.0, le=10.0, default=0.0)
    status: str = Field(pattern="^(ongoing|completed|upcoming)$", default="completed")


class AdminCharacterUpdateRequest(BaseModel):
    locale: str = Field(pattern="^(ru|en)$", default="ru")
    name: str = Field(min_length=1, max_length=200)
    bio: str = ""
    spoiler_bio: str = ""
    image_url: str = ""
    hair_color: str = "unknown"
    eye_color: str = "unknown"
    mbti: str = ""
    archetype: str = ""
