import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Revision
from app.schemas import (
    BatchImportRequest,
    ImportQueryRequest,
    LoginRequest,
    RegisterRequest,
    RevisionCreateRequest,
    RevisionDecisionRequest,
    TraitVoteRequest,
    UserListUpsertRequest,
)
from app.services.auth import (
    SESSION_COOKIE_NAME,
    authenticate_user,
    create_session_token,
    get_current_user,
    get_profile_key,
    register_user,
)
from app.services.catalog import (
    create_revision,
    decide_revision,
    list_characters,
    list_media,
    pick_char_translation,
    pick_media_translation,
    upsert_user_list,
    vote_character_trait,
)
from app.services.importers.anilist import import_batch_from_anilist, import_from_anilist
from app.services.importers.igdb import IGDBNotConfiguredError, import_from_igdb
from app.services.importers.vndb import import_from_vndb
from app.services.search import global_search

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "Kiroku"}


@router.get("/search")
def api_search(
    q: str = Query(default="", min_length=0),
    locale: str = Query(default="ru"),
    media_type: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
):
    return global_search(db, q, locale=locale, media_type=media_type, limit=limit)


@router.get("/media")
def api_list_media(
    locale: str = "ru",
    q: str | None = None,
    media_type: str | None = None,
    franchise: str | None = None,
    genre: str | None = None,
    sort: str = "popularity",
    limit: int = 60,
    db: Session = Depends(get_db),
):
    items = list_media(
        db,
        query=q,
        media_type=media_type,
        franchise_slug=franchise,
        genre=genre,
        sort=sort,
        limit=limit,
    )
    results = []
    for m in items:
        tr = pick_media_translation(m, locale)
        results.append(
            {
                "id": m.id,
                "slug": m.slug,
                "media_type": m.media_type,
                "title": tr.title if tr else m.canonical_title,
                "canonical_title": m.canonical_title,
                "release_year": m.release_year,
                "rating": m.rating,
                "popularity": m.popularity,
                "genres": m.genres,
            }
        )
    return {"items": results, "count": len(results)}


@router.get("/characters")
def api_list_characters(
    locale: str = "ru",
    q: str | None = None,
    media_type: str | None = None,
    gender: str | None = None,
    hair_color: str | None = None,
    eye_color: str | None = None,
    mbti: str | None = None,
    trait: str | None = None,
    limit: int = 60,
    db: Session = Depends(get_db),
):
    chars = list_characters(
        db,
        query=q,
        media_type=media_type,
        gender=gender,
        hair_color=hair_color,
        eye_color=eye_color,
        mbti=mbti,
        trait_slug=trait,
        limit=limit,
    )
    results = []
    for c in chars:
        tr = pick_char_translation(c, locale)
        results.append(
            {
                "id": c.id,
                "slug": c.slug,
                "name": tr.name if tr else c.canonical_name,
                "canonical_name": c.canonical_name,
                "hair_color": c.hair_color,
                "eye_color": c.eye_color,
                "mbti": c.mbti,
                "archetype": c.archetype,
            }
        )
    return {"items": results, "count": len(results)}


@router.post("/auth/register")
def api_register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    try:
        user = register_user(
            db,
            username=payload.username,
            email=payload.email,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token = create_session_token(user.id)
    response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
    return {"id": user.id, "username": user.username, "email": user.email}


@router.post("/auth/login")
def api_login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = authenticate_user(db, login=payload.login, password=payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    token = create_session_token(user.id)
    response.set_cookie(SESSION_COOKIE_NAME, token, httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
    return {"id": user.id, "username": user.username, "email": user.email}


@router.post("/auth/logout")
def api_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "logged_out"}


@router.get("/auth/me")
def api_me(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {"id": user.id, "username": user.username, "email": user.email, "role": user.role},
    }


@router.post("/traits/vote")
def api_vote_trait(
    payload: TraitVoteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if user:
        voter_key = f"user:{user.id}"
    else:
        voter_key = request.cookies.get("kiroku_voter_id")
        if not voter_key:
            voter_key = uuid.uuid4().hex
            response.set_cookie("kiroku_voter_id", voter_key, max_age=60 * 60 * 24 * 365, samesite="lax")
    try:
        votes, voted = vote_character_trait(db, payload.character_id, payload.trait_slug, voter_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "character_id": payload.character_id,
        "trait_slug": payload.trait_slug,
        "votes_count": votes,
        "voted": voted,
    }


@router.get("/revisions")
def api_list_revisions(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Revision).order_by(desc(Revision.created_at)).limit(50)
    if status:
        stmt = stmt.where(Revision.status == status)
    revs = list(db.scalars(stmt).all())
    return {
        "items": [
            {
                "id": r.id,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "locale": r.locale,
                "author_name": r.author_name,
                "field_name": r.field_name,
                "previous_value": r.previous_value,
                "proposed_value": r.proposed_value,
                "reason": r.reason,
                "status": r.status,
            }
            for r in revs
        ]
    }


@router.post("/revisions")
def api_create_revision(payload: RevisionCreateRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    author = user.username if user else payload.author_name
    rev = create_revision(
        db,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        locale=payload.locale,
        author_name=author,
        field_name=payload.field_name,
        proposed_value=payload.proposed_value,
        reason=payload.reason,
    )
    return {"id": rev.id, "status": rev.status}


@router.post("/revisions/{revision_id}/decision")
def api_decide_revision(
    revision_id: int,
    payload: RevisionDecisionRequest,
    db: Session = Depends(get_db),
):
    try:
        rev = decide_revision(db, revision_id, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": rev.id, "status": rev.status}


@router.post("/lists")
def api_upsert_list(payload: UserListUpsertRequest, request: Request, db: Session = Depends(get_db)):
    profile_key, _user = get_profile_key(request, db)
    entry = upsert_user_list(
        db,
        media_id=payload.media_id,
        status=payload.status,
        progress_unit=payload.progress_unit,
        score=payload.score,
        is_favorite=payload.is_favorite,
        notes=payload.notes,
        profile_key=profile_key,
    )
    return {
        "id": entry.id,
        "profile_key": profile_key,
        "media_id": entry.media_id,
        "status": entry.status,
        "progress_unit": entry.progress_unit,
        "score": entry.score,
        "is_favorite": entry.is_favorite,
    }


@router.post("/import/batch")
async def api_import_batch(payload: BatchImportRequest, db: Session = Depends(get_db)):
    try:
        result = await import_batch_from_anilist(
            db,
            category=payload.category,
            per_page=payload.count,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {exc}") from exc
    return result


@router.post("/import")
async def api_import_external(payload: ImportQueryRequest, db: Session = Depends(get_db)):
    try:
        if payload.provider == "anilist":
            items = await import_from_anilist(
                db,
                query=payload.query,
                media_type=payload.media_type,
                limit=payload.limit,
            )
        elif payload.provider == "vndb":
            items = await import_from_vndb(db, query=payload.query, limit=payload.limit)
        elif payload.provider == "igdb":
            items = await import_from_igdb(db, query=payload.query, limit=payload.limit)
        else:
            raise HTTPException(status_code=400, detail="Unsupported provider")
    except IGDBNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {exc}") from exc

    return {"provider": payload.provider, "imported_count": len(items), "items": items}
