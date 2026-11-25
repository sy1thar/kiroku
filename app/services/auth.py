import hashlib
import hmac
import secrets
from fastapi import HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.roles import USER_ROLES_TABLE

SESSION_COOKIE_NAME = "kiroku_session"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest_hex = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return hmac.compare_digest(dk.hex(), digest_hex)
    except ValueError:
        return False


def create_session_token(user_id: int) -> str:
    secret = get_settings().session_secret.encode("utf-8")
    payload = str(user_id)
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    try:
        payload, sig = token.split(".", 1)
        secret = get_settings().session_secret.encode("utf-8")
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return int(payload)
    except (ValueError, TypeError):
        return None
    return None


def _resolve_role_override(username: str, email: str) -> str | None:
    settings = get_settings()
    uname_low = username.strip().lower()
    email_low = email.strip().lower()
    if uname_low in settings.admin_username_set or email_low in settings.admin_email_set:
        return "admin"
    table = {k.strip().lower(): v for k, v in USER_ROLES_TABLE.items() if k.strip()}
    return table.get(uname_low)


def _is_configured_admin(username: str, email: str) -> bool:
    return _resolve_role_override(username, email) == "admin"


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME))
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user:
        override = _resolve_role_override(user.username, user.email)
        if override and user.role != override:
            user.role = override
            db.commit()
            db.refresh(user)
    return user


def require_user(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_moderator_or_admin(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if user.role not in ("moderator", "admin"):
        raise HTTPException(status_code=403, detail="Moderator or Admin role required")
    return user


def require_admin(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def get_profile_key(request: Request, db: Session) -> tuple[str, User | None]:
    user = get_current_user(request, db)
    if user:
        return f"user:{user.id}", user
    return "guest", None


def register_user(db: Session, *, username: str, email: str, password: str) -> User:
    clean_username = username.strip()
    clean_email = email.strip().lower()
    if "@" not in clean_email:
        raise ValueError("Invalid email format")

    existing = db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == clean_username.lower(),
                func.lower(User.email) == clean_email,
            )
        )
    )
    if existing:
        raise ValueError("Username or email already registered")

    role = _resolve_role_override(clean_username, clean_email) or "user"
    user = User(
        username=clean_username,
        email=clean_email,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, *, login: str, password: str) -> User | None:
    key = login.strip()
    user = db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == key.lower(),
                func.lower(User.email) == key.lower(),
            )
        )
    )
    if not user or not verify_password(password, user.password_hash):
        return None
    if user.role != "admin" and _is_configured_admin(user.username, user.email):
        user.role = "admin"
        db.commit()
        db.refresh(user)
    return user


def update_user_profile(
    db: Session,
    user: User,
    *,
    username: str,
    email: str,
    avatar_url: str,
    bio: str,
    preferred_locale: str,
) -> User:
    clean_username = username.strip()
    clean_email = email.strip().lower()
    if "@" not in clean_email:
        raise ValueError("Invalid email format")

    conflict = db.scalar(
        select(User).where(
            User.id != user.id,
            or_(
                func.lower(User.username) == clean_username.lower(),
                func.lower(User.email) == clean_email,
            ),
        )
    )
    if conflict:
        raise ValueError("Username or email is already taken")

    clean_avatar = avatar_url.strip()
    if clean_avatar and not (
        clean_avatar.startswith("https://")
        or clean_avatar.startswith("http://")
        or clean_avatar.startswith("data:image/")
    ):
        raise ValueError("Avatar must be an http(s) URL or image data URL")

    user.username = clean_username
    user.email = clean_email
    user.avatar_url = clean_avatar
    user.bio = bio.strip()
    user.preferred_locale = preferred_locale if preferred_locale in ("ru", "en") else "ru"
    if _is_configured_admin(user.username, user.email):
        user.role = "admin"
    db.commit()
    db.refresh(user)
    return user


def change_user_password(db: Session, user: User, *, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Current password is incorrect")
    user.password_hash = hash_password(new_password)
    db.commit()
