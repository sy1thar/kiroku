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
    category: str = Field(pattern="^(anime|manga|manhwa)$")
    count: int = Field(ge=5, le=15, default=12)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern="^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    login: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=6, max_length=128)
