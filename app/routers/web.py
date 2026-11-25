import secrets
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.i18n import (
    COLOR_LABELS,
    GENDER_LABELS,
    LIST_STATUS_LABELS,
    MEDIA_TYPE_LABELS,
    ROLE_LABELS,
    SOURCE_BADGES,
    t_ui,
)
from app.models import Character, Franchise, Media, Revision, SyncCursor, Trait, UserListEntry
from app.services.auth import SESSION_COOKIE_NAME, create_session_token, get_current_user, get_profile_key
from app.services.oauth import (
    build_oauth_authorize_url,
    exchange_oauth_code,
    find_or_create_oauth_user,
    get_oauth_providers,
    verify_telegram_login,
)
from app.services.catalog import (
    get_character_detail,
    get_media_detail,
    get_voted_trait_slugs,
    list_characters,
    list_media,
    pick_char_translation,
    pick_media_translation,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
router = APIRouter(tags=["web"])


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml")


def _resolve_locale(request: Request, explicit_locale: str | None = None) -> str:
    if explicit_locale in ("ru", "en"):
        return explicit_locale
    q_lang = request.query_params.get("lang")
    if q_lang in ("ru", "en"):
        return q_lang
    c_lang = request.cookies.get("kiroku_locale")
    if c_lang in ("ru", "en"):
        return c_lang
    return get_settings().default_locale


def _base_context(
    request: Request,
    locale: str,
    page_title: str,
    path_suffix: str = "",
    db: Session | None = None,
) -> dict:
    current_user = get_current_user(request, db) if db is not None else None
    return {
        "request": request,
        "locale": locale,
        "other_locale": "en" if locale == "ru" else "ru",
        "ui": t_ui(locale),
        "page_title": page_title,
        "path_suffix": path_suffix,
        "current_user": current_user,
        "media_type_labels": MEDIA_TYPE_LABELS,
        "color_labels": COLOR_LABELS,
        "gender_labels": GENDER_LABELS,
        "role_labels": ROLE_LABELS,
        "source_badges": SOURCE_BADGES,
        "list_status_labels": LIST_STATUS_LABELS,
        "pick_media_tr": lambda m: pick_media_translation(m, locale),
        "pick_char_tr": lambda c: pick_char_translation(c, locale),
    }


@router.get("/set-locale/{locale}")
def set_locale(locale: str, next: str = "/"):
    target_loc = locale if locale in ("ru", "en") else "ru"
    safe_next = next if next.startswith("/") else "/"
    # Rewrite /ru/ or /en/ prefix if present
    if safe_next.startswith("/ru/") or safe_next.startswith("/en/"):
        safe_next = f"/{target_loc}/" + safe_next[4:]
    elif safe_next in ("/ru", "/en"):
        safe_next = f"/{target_loc}"
    resp = RedirectResponse(url=safe_next, status_code=302)
    resp.set_cookie("kiroku_locale", target_loc, max_age=60 * 60 * 24 * 365, samesite="lax")
    return resp


def _render_home(request: Request, locale: str, db: Session) -> HTMLResponse:
    trending_media = list_media(db, limit=8)
    featured_chars = list_characters(db, limit=8)
    franchises = list(
        db.scalars(
            select(Franchise).options(selectinload(Franchise.media_items))
        ).all()
    )
    stats = {
        "media_count": db.scalar(select(func.count(Media.id))) or 0,
        "char_count": db.scalar(select(func.count(Character.id))) or 0,
        "franchise_count": len(franchises),
    }
    ctx = _base_context(request, locale, f"Kiroku — {t_ui(locale)['brand_tagline']}", "", db)
    ctx.update(
        {
            "trending_media": trending_media,
            "featured_chars": featured_chars,
            "franchises": franchises,
            "stats": stats,
        }
    )
    return templates.TemplateResponse(request, "home.html", ctx)


@router.get("/", response_class=HTMLResponse)
def home_root(request: Request, db: Session = Depends(get_db)):
    return _render_home(request, _resolve_locale(request), db)


@router.get("/{locale}", response_class=HTMLResponse)
def home_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404, detail="Page not found")
    return _render_home(request, locale, db)


def _render_catalog(
    request: Request,
    locale: str,
    db: Session,
    q: str | None = None,
    media_type: str | None = None,
    franchise: str | None = None,
    genre: str | None = None,
    sort: str = "popularity",
) -> HTMLResponse:
    items = list_media(
        db,
        query=q or None,
        media_type=media_type or None,
        franchise_slug=franchise or None,
        genre=genre or None,
        sort=sort,
        limit=60,
    )
    active_franchise = (
        db.scalar(select(Franchise).where(Franchise.slug == franchise))
        if franchise
        else None
    )
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_catalog']} — Kiroku", "/catalog", db)
    ctx.update(
        {
            "items": items,
            "active_franchise": active_franchise,
            "selected_q": q or "",
            "selected_type": media_type or "",
            "selected_franchise": franchise or "",
            "selected_genre": genre or "",
            "selected_sort": sort,
        }
    )
    return templates.TemplateResponse(request, "catalog.html", ctx)


@router.get("/catalog", response_class=HTMLResponse)
def catalog_root(
    request: Request,
    q: str | None = None,
    type: str | None = None,
    franchise: str | None = None,
    genre: str | None = None,
    sort: str = "popularity",
    db: Session = Depends(get_db),
):
    return _render_catalog(request, _resolve_locale(request), db, q, type, franchise, genre, sort)


@router.get("/{locale}/catalog", response_class=HTMLResponse)
def catalog_localized(
    locale: str,
    request: Request,
    q: str | None = None,
    type: str | None = None,
    franchise: str | None = None,
    genre: str | None = None,
    sort: str = "popularity",
    db: Session = Depends(get_db),
):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_catalog(request, locale, db, q, type, franchise, genre, sort)


@router.get("/{locale}/media/{slug}", response_class=HTMLResponse)
def media_detail_page(locale: str, slug: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    profile_key, _user = get_profile_key(request, db)
    data = get_media_detail(db, slug, profile_key=profile_key)
    if not data:
        raise HTTPException(status_code=404, detail="Media not found")
    media = data["media"]
    tr = pick_media_translation(media, locale)
    title = tr.title if tr else media.canonical_title
    ctx = _base_context(request, locale, f"{title} — Kiroku", f"/media/{slug}", db)
    ctx.update(data)
    ctx["tr"] = tr
    return templates.TemplateResponse(request, "media_detail.html", ctx)


def _render_characters(
    request: Request,
    locale: str,
    db: Session,
    q: str | None = None,
    media_type: str | None = None,
    gender: str | None = None,
    hair_color: str | None = None,
    eye_color: str | None = None,
    mbti: str | None = None,
    trait: str | None = None,
) -> HTMLResponse:
    chars = list_characters(
        db,
        query=q or None,
        media_type=media_type or None,
        gender=gender or None,
        hair_color=hair_color or None,
        eye_color=eye_color or None,
        mbti=mbti or None,
        trait_slug=trait or None,
    )
    traits = list(db.scalars(select(Trait).order_by(Trait.category, Trait.name_en)).all())
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_characters']} — Kiroku", "/characters", db)
    ctx.update(
        {
            "characters": chars,
            "traits": traits,
            "selected_q": q or "",
            "selected_type": media_type or "",
            "selected_gender": gender or "",
            "selected_hair": hair_color or "",
            "selected_eye": eye_color or "",
            "selected_mbti": mbti or "",
            "selected_trait": trait or "",
            "mbti_options": ["INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ISTP", "ISTJ", "ISFJ", "ENFP", "ISFP", "ESTP"],
        }
    )
    return templates.TemplateResponse(request, "characters.html", ctx)


@router.get("/characters", response_class=HTMLResponse)
def characters_root(
    request: Request,
    q: str | None = None,
    type: str | None = None,
    gender: str | None = None,
    hair: str | None = None,
    eye: str | None = None,
    mbti: str | None = None,
    trait: str | None = None,
    db: Session = Depends(get_db),
):
    return _render_characters(request, _resolve_locale(request), db, q, type, gender, hair, eye, mbti, trait)


@router.get("/{locale}/characters", response_class=HTMLResponse)
def characters_localized(
    locale: str,
    request: Request,
    q: str | None = None,
    type: str | None = None,
    gender: str | None = None,
    hair: str | None = None,
    eye: str | None = None,
    mbti: str | None = None,
    trait: str | None = None,
    db: Session = Depends(get_db),
):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_characters(request, locale, db, q, type, gender, hair, eye, mbti, trait)


@router.get("/{locale}/characters/{slug}", response_class=HTMLResponse)
def character_detail_page(locale: str, slug: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    data = get_character_detail(db, slug)
    if not data:
        raise HTTPException(status_code=404, detail="Character not found")
    ch = data["character"]
    tr = pick_char_translation(ch, locale)
    name = tr.name if tr else ch.canonical_name
    profile_key, user = get_profile_key(request, db)
    voter_key = f"user:{user.id}" if user else request.cookies.get("kiroku_voter_id", "")
    ctx = _base_context(request, locale, f"{name} — Kiroku", f"/characters/{slug}", db)
    ctx.update(data)
    ctx["tr"] = tr
    ctx["voted_traits"] = get_voted_trait_slugs(db, ch.id, voter_key)
    return templates.TemplateResponse(request, "character_detail.html", ctx)


def _render_import_hub(request: Request, locale: str, db: Session) -> HTMLResponse:
    settings = get_settings()
    cursors = {
        c.category: c
        for c in db.scalars(select(SyncCursor).where(SyncCursor.provider == "anilist")).all()
    }
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_import']} — Kiroku", "/import", db)
    ctx["igdb_ready"] = bool(settings.twitch_client_id and settings.twitch_client_secret)
    ctx["cursors"] = cursors
    return templates.TemplateResponse(request, "import_hub.html", ctx)


@router.get("/import", response_class=HTMLResponse)
def import_root(request: Request, db: Session = Depends(get_db)):
    return _render_import_hub(request, _resolve_locale(request), db)


@router.get("/{locale}/import", response_class=HTMLResponse)
def import_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_import_hub(request, locale, db)


def _render_moderation(request: Request, locale: str, db: Session) -> HTMLResponse:
    revs = list(db.scalars(select(Revision).order_by(desc(Revision.created_at)).limit(50)).all())
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_moderation']} — Kiroku", "/moderation", db)
    ctx["revisions"] = revs
    return templates.TemplateResponse(request, "moderation.html", ctx)


@router.get("/moderation", response_class=HTMLResponse)
def moderation_root(request: Request, db: Session = Depends(get_db)):
    return _render_moderation(request, _resolve_locale(request), db)


@router.get("/{locale}/moderation", response_class=HTMLResponse)
def moderation_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_moderation(request, locale, db)


def _render_lists(request: Request, locale: str, db: Session) -> HTMLResponse:
    profile_key, _user = get_profile_key(request, db)
    entries = list(
        db.scalars(
            select(UserListEntry)
            .where(UserListEntry.profile_key == profile_key)
            .options(selectinload(UserListEntry.media).selectinload(Media.translations))
            .order_by(desc(UserListEntry.updated_at))
        ).all()
    )
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_lists']} — Kiroku", "/lists", db)
    ctx["entries"] = entries
    return templates.TemplateResponse(request, "my_kiroku.html", ctx)


@router.get("/lists", response_class=HTMLResponse)
def lists_root(request: Request, db: Session = Depends(get_db)):
    return _render_lists(request, _resolve_locale(request), db)


@router.get("/{locale}/lists", response_class=HTMLResponse)
def lists_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_lists(request, locale, db)


def _render_about(request: Request, locale: str, db: Session) -> HTMLResponse:
    stats = {
        "media_count": db.scalar(select(func.count(Media.id))) or 0,
        "char_count": db.scalar(select(func.count(Character.id))) or 0,
        "franchise_count": db.scalar(select(func.count(Franchise.id))) or 0,
    }
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_about']} — Kiroku", "/about", db)
    ctx["stats"] = stats
    return templates.TemplateResponse(request, "about.html", ctx)


@router.get("/about", response_class=HTMLResponse)
def about_root(request: Request, db: Session = Depends(get_db)):
    return _render_about(request, _resolve_locale(request), db)


@router.get("/{locale}/about", response_class=HTMLResponse)
def about_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_about(request, locale, db)


def _auth_context(request: Request, locale: str, page_key: str, db: Session, oauth_notice: str = "") -> dict:
    ctx = _base_context(request, locale, f"{t_ui(locale)[page_key]} — Kiroku", f"/{page_key.replace('nav_', '')}", db)
    ctx["oauth_providers"] = get_oauth_providers()
    ctx["oauth_notice"] = oauth_notice
    ctx["telegram_bot_username"] = get_settings().telegram_bot_username
    return ctx


@router.get("/login", response_class=HTMLResponse)
def login_root(request: Request, notice: str = "", db: Session = Depends(get_db)):
    loc = _resolve_locale(request)
    return templates.TemplateResponse(request, "login.html", _auth_context(request, loc, "nav_login", db, notice))


@router.get("/{locale}/login", response_class=HTMLResponse)
def login_localized(locale: str, request: Request, notice: str = "", db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "login.html", _auth_context(request, locale, "nav_login", db, notice))


@router.get("/register", response_class=HTMLResponse)
def register_root(request: Request, notice: str = "", db: Session = Depends(get_db)):
    loc = _resolve_locale(request)
    return templates.TemplateResponse(request, "register.html", _auth_context(request, loc, "nav_register", db, notice))


@router.get("/{locale}/register", response_class=HTMLResponse)
def register_localized(locale: str, request: Request, notice: str = "", db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "register.html", _auth_context(request, locale, "nav_register", db, notice))


@router.get("/auth/oauth/{provider}")
def oauth_start(provider: str, request: Request, db: Session = Depends(get_db)):
    loc = _resolve_locale(request)
    providers = {p["id"]: p for p in get_oauth_providers()}
    info = providers.get(provider)
    if not info:
        raise HTTPException(status_code=404, detail="Unknown OAuth provider")

    if provider == "telegram":
        if not info["configured"]:
            msg = (
                f"Telegram Login: добавьте {info['env_hint']} в .env для включения входа через Telegram."
                if loc == "ru"
                else f"Telegram Login: set {info['env_hint']} in .env to enable Telegram sign-in."
            )
            return templates.TemplateResponse(request, "register.html", _auth_context(request, loc, "nav_register", db, msg))
        return templates.TemplateResponse(request, "register.html", _auth_context(request, loc, "nav_register", db))

    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    state = secrets.token_urlsafe(24)
    auth_url = build_oauth_authorize_url(provider, redirect_uri, state)
    if not auth_url:
        msg = (
            f"OAuth ({info['name']}): добавьте {info['env_hint']} в .env для реальной авторизации."
            if loc == "ru"
            else f"OAuth ({info['name']}): add {info['env_hint']} to .env to enable live sign-in."
        )
        return templates.TemplateResponse(request, "register.html", _auth_context(request, loc, "nav_register", db, msg))

    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie("kiroku_oauth_state", state, httponly=True, max_age=600, samesite="lax")
    return resp


@router.get("/auth/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
):
    loc = _resolve_locale(request)
    expected_state = request.cookies.get("kiroku_oauth_state", "")
    if not code or not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state or missing code")

    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    uid, uname, email = await exchange_oauth_code(provider, code, redirect_uri)
    user = find_or_create_oauth_user(
        db,
        provider=provider,
        provider_user_id=uid,
        username_hint=uname,
        email_hint=email,
    )
    resp = RedirectResponse(url=f"/{loc}/lists", status_code=302)
    resp.delete_cookie("kiroku_oauth_state")
    resp.set_cookie(SESSION_COOKIE_NAME, create_session_token(user.id), httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
    return resp


@router.get("/auth/telegram/callback")
def telegram_callback(request: Request, db: Session = Depends(get_db)):
    loc = _resolve_locale(request)
    params = dict(request.query_params)
    try:
        uid, uname = verify_telegram_login(params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = find_or_create_oauth_user(db, provider="telegram", provider_user_id=uid, username_hint=uname)
    resp = RedirectResponse(url=f"/{loc}/lists", status_code=302)
    resp.set_cookie(SESSION_COOKIE_NAME, create_session_token(user.id), httponly=True, max_age=60 * 60 * 24 * 30, samesite="lax")
    return resp


@router.get("/logout")
def logout_redirect(request: Request):
    loc = _resolve_locale(request)
    resp = RedirectResponse(url=f"/{loc}", status_code=302)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp
