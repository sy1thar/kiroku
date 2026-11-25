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
from app.models import Character, Franchise, Media, Revision, SyncCursor, Trait, User, UserListEntry
from app.services.auth import SESSION_COOKIE_NAME, get_current_user, get_profile_key, require_admin, require_user
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
    require_admin(request, db)
    settings = get_settings()
    cursors = {
        c.category: c
        for c in db.scalars(select(SyncCursor)).all()
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
    user = get_current_user(request, db)
    can_moderate = bool(user and user.role in ("moderator", "admin"))
    revs = list(db.scalars(select(Revision).order_by(desc(Revision.created_at)).limit(50)).all())
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_moderation']} — Kiroku", "/moderation", db)
    ctx["revisions"] = revs
    ctx["can_moderate"] = can_moderate
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


@router.get("/login", response_class=HTMLResponse)
def login_root(request: Request, db: Session = Depends(get_db)):
    loc = _resolve_locale(request)
    return templates.TemplateResponse(request, "login.html", _base_context(request, loc, f"{t_ui(loc)['nav_login']} — Kiroku", "/login", db))


@router.get("/{locale}/login", response_class=HTMLResponse)
def login_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "login.html", _base_context(request, locale, f"{t_ui(locale)['nav_login']} — Kiroku", "/login", db))


@router.get("/register", response_class=HTMLResponse)
def register_root(request: Request, db: Session = Depends(get_db)):
    loc = _resolve_locale(request)
    return templates.TemplateResponse(request, "register.html", _base_context(request, loc, f"{t_ui(loc)['nav_register']} — Kiroku", "/register", db))


@router.get("/{locale}/register", response_class=HTMLResponse)
def register_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request, "register.html", _base_context(request, locale, f"{t_ui(locale)['nav_register']} — Kiroku", "/register", db))


@router.get("/settings", response_class=HTMLResponse)
def settings_root(request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    loc = _resolve_locale(request)
    return templates.TemplateResponse(request, "settings.html", _base_context(request, loc, f"{t_ui(loc)['nav_settings']} — Kiroku", "/settings", db))


@router.get("/{locale}/settings", response_class=HTMLResponse)
def settings_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    require_user(request, db)
    return templates.TemplateResponse(request, "settings.html", _base_context(request, locale, f"{t_ui(locale)['nav_settings']} — Kiroku", "/settings", db))


def _render_admin(request: Request, locale: str, db: Session) -> HTMLResponse:
    require_admin(request, db)
    users = list(db.scalars(select(User).order_by(User.id)).all())
    media_items = list_media(db, limit=100)
    characters = list_characters(db, limit=100)
    ctx = _base_context(request, locale, f"{t_ui(locale)['nav_admin']} — Kiroku", "/admin", db)
    ctx.update({"users": users, "media_items": media_items, "characters": characters})
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.get("/admin", response_class=HTMLResponse)
def admin_root(request: Request, db: Session = Depends(get_db)):
    return _render_admin(request, _resolve_locale(request), db)


@router.get("/{locale}/admin", response_class=HTMLResponse)
def admin_localized(locale: str, request: Request, db: Session = Depends(get_db)):
    if locale not in ("ru", "en"):
        raise HTTPException(status_code=404)
    return _render_admin(request, locale, db)


@router.get("/logout")
def logout_redirect(request: Request):
    loc = _resolve_locale(request)
    resp = RedirectResponse(url=f"/{loc}", status_code=302)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp
