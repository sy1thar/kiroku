import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
import httpx
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Character, CharacterTranslation, Media, MediaTranslation, Revision, User
from app.schemas import (
    AccountProfileUpdateRequest,
    AdminCharacterUpdateRequest,
    AdminImportCharactersRequest,
    AdminMediaUpdateRequest,
    AdminRoleUpdateRequest,
    AdminUsernameRoleRequest,
    BatchImportRequest,
    ImportQueryRequest,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    RevisionCreateRequest,
    RevisionDecisionRequest,
    TraitVoteRequest,
    UserListUpsertRequest,
)
from app.services.auth import (
    SESSION_COOKIE_NAME,
    authenticate_user,
    change_user_password,
    create_session_token,
    get_current_user,
    get_profile_key,
    register_user,
    require_admin,
    require_moderator_or_admin,
    require_user,
    update_user_profile,
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
from app.services.importers.anilist import import_from_anilist, import_more_characters_for_media
from app.services.importers.batch import run_batch_import
from app.services.importers.igdb import IGDBNotConfiguredError, import_from_igdb
from app.services.importers.vndb import import_from_vndb
from app.services.search import global_search
from app.services.translation import (
    _has_cyrillic,
    fetch_shikimori_character_ru,
    fetch_shikimori_ru,
    translate_text,
)

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
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "avatar_url": user.avatar_url,
            "bio": user.bio,
            "preferred_locale": user.preferred_locale,
        },
    }


@router.patch("/account/profile")
def api_update_profile(
    payload: AccountProfileUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    try:
        updated = update_user_profile(
            db,
            user,
            username=payload.username,
            email=payload.email,
            avatar_url=payload.avatar_url,
            bio=payload.bio,
            preferred_locale=payload.preferred_locale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": updated.id,
        "username": updated.username,
        "email": updated.email,
        "avatar_url": updated.avatar_url,
        "bio": updated.bio,
        "preferred_locale": updated.preferred_locale,
        "role": updated.role,
    }


@router.post("/account/password")
def api_change_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    try:
        change_user_password(
            db,
            user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "password_updated"}


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
    request: Request,
    db: Session = Depends(get_db),
):
    require_moderator_or_admin(request, db)
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
async def api_import_batch(
    payload: BatchImportRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    try:
        result = await run_batch_import(db, category=payload.category, count=payload.count)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/import")
async def api_import_external(
    payload: ImportQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
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


# --- Admin Endpoints (Admin Only) ---


@router.get("/admin/users")
def api_admin_list_users(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    users = list(db.scalars(select(User).order_by(User.id)).all())
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "avatar_url": u.avatar_url,
            }
            for u in users
        ]
    }


@router.post("/admin/users/{user_id}/role")
def api_admin_set_user_role(
    user_id: int,
    payload: AdminRoleUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    admin_user = require_admin(request, db)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin_user.id and payload.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot demote yourself")
    target.role = payload.role
    db.commit()
    return {"id": target.id, "username": target.username, "role": target.role}


@router.delete("/admin/users/{user_id}")
def api_admin_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin_user = require_admin(request, db)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == admin_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db.delete(target)
    db.commit()
    return {"deleted": user_id}


@router.patch("/admin/media/{media_id}")
def api_admin_update_media(
    media_id: int,
    payload: AdminMediaUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    media = db.get(Media, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    media.cover_url = payload.cover_url.strip()
    media.rating = payload.rating
    media.status = payload.status
    tr = db.scalar(
        select(MediaTranslation).where(
            MediaTranslation.media_id == media.id,
            MediaTranslation.locale == payload.locale,
        )
    )
    if not tr:
        tr = MediaTranslation(media_id=media.id, locale=payload.locale, title=payload.title)
        db.add(tr)
    tr.title = payload.title.strip()
    tr.synopsis = payload.synopsis.strip()
    tr.source = "community_verified"
    db.commit()
    return {"id": media.id, "slug": media.slug, "status": "updated"}


@router.delete("/admin/media/{media_id}")
def api_admin_delete_media(media_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    media = db.get(Media, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    db.delete(media)
    db.commit()
    return {"deleted": media_id}


@router.patch("/admin/characters/{character_id}")
def api_admin_update_character(
    character_id: int,
    payload: AdminCharacterUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    ch = db.get(Character, character_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Character not found")
    ch.image_url = payload.image_url.strip()
    ch.hair_color = payload.hair_color
    ch.eye_color = payload.eye_color
    ch.mbti = payload.mbti.upper().strip()
    ch.archetype = payload.archetype.strip()
    tr = db.scalar(
        select(CharacterTranslation).where(
            CharacterTranslation.character_id == ch.id,
            CharacterTranslation.locale == payload.locale,
        )
    )
    if not tr:
        tr = CharacterTranslation(character_id=ch.id, locale=payload.locale, name=payload.name)
        db.add(tr)
    tr.name = payload.name.strip()
    tr.bio = payload.bio.strip()
    tr.spoiler_bio = payload.spoiler_bio.strip()
    tr.source = "community_verified"
    db.commit()
    return {"id": ch.id, "slug": ch.slug, "status": "updated"}


@router.post("/admin/users/by-username/role")
def api_admin_set_role_by_username(
    payload: AdminUsernameRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    admin_user = require_admin(request, db)
    target = db.scalar(select(User).where(func.lower(User.username) == payload.username.strip().lower()))
    if not target:
        raise HTTPException(status_code=404, detail=f"User '{payload.username}' not found")
    if target.id == admin_user.id and payload.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot demote yourself")
    target.role = payload.role
    db.commit()
    return {"id": target.id, "username": target.username, "role": target.role}


@router.post("/admin/media/{media_id}/import-characters")
async def api_admin_import_media_characters(
    media_id: int,
    payload: AdminImportCharactersRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    try:
        res = await import_more_characters_for_media(db, media_id, count=payload.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {exc}") from exc
    return res


@router.post("/admin/translate-missing")
async def api_admin_translate_missing(request: Request, db: Session = Depends(get_db)):
    """Localize up to 15 untranslated titles/descriptions & 30 characters per click to Russian."""
    require_admin(request, db)
    fixed_media = 0
    fixed_chars = 0

    media_rows = db.scalars(select(Media).options(selectinload(Media.translations))).all()
    for m in media_rows:
        if fixed_media >= 15:
            break
        if m.slug in ("elden-ring-game", "persona-5-royal-game"):
            continue
        ru_tr = next((t for t in m.translations if t.locale == "ru"), None)
        en_tr = next((t for t in m.translations if t.locale == "en"), None)
        if not ru_tr:
            continue
        needs_title = not _has_cyrillic(ru_tr.title)
        needs_syn = bool(en_tr and en_tr.synopsis and not _has_cyrillic(ru_tr.synopsis))
        if not needs_title and not needs_syn:
            continue

        kind = "animes" if m.media_type == "anime" else "mangas"
        ru_title, ru_desc = await fetch_shikimori_ru(kind=kind, search_title=m.canonical_title)
        if needs_title:
            if not ru_title:
                ru_title, _ = await translate_text(m.canonical_title, "ru")
            if ru_title and _has_cyrillic(ru_title):
                ru_tr.title = ru_title
        if needs_syn:
            if ru_desc and _has_cyrillic(ru_desc):
                ru_tr.synopsis = ru_desc
                ru_tr.source = "community_verified"
            elif en_tr and en_tr.synopsis:
                tr_syn, src = await translate_text(en_tr.synopsis, "ru")
                if tr_syn and _has_cyrillic(tr_syn):
                    ru_tr.synopsis = tr_syn
                    ru_tr.source = src
        fixed_media += 1

    char_rows = db.scalars(select(Character).options(selectinload(Character.translations))).all()
    for c in char_rows:
        if fixed_chars >= 30:
            break
        ru_tr = next((t for t in c.translations if t.locale == "ru"), None)
        if not ru_tr or _has_cyrillic(ru_tr.name):
            continue
        rn = await fetch_shikimori_character_ru(c.canonical_name)
        if not rn:
            rn, _ = await translate_text(c.canonical_name, "ru")
        if rn and _has_cyrillic(rn):
            ru_tr.name = rn
            fixed_chars += 1

    db.commit()
    return {"translated_media": fixed_media, "translated_characters": fixed_chars}


@router.delete("/admin/characters/{character_id}")
def api_admin_delete_character(character_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    ch = db.get(Character, character_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Character not found")
    db.delete(ch)
    db.commit()
    return {"deleted": character_id}
