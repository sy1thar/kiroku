import hashlib
import hmac
import secrets
from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User

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


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME))
    if not user_id:
        return None
    return db.get(User, user_id)


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
        select(User).where(or_(User.username == clean_username, User.email == clean_email))
    )
    if existing:
        raise ValueError("Username or email already registered")

    user = User(
        username=clean_username,
        email=clean_email,
        password_hash=hash_password(password),
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, *, login: str, password: str) -> User | None:
    key = login.strip()
    user = db.scalar(
        select(User).where(or_(User.username == key, User.email == key.lower()))
    )
    if not user or not verify_password(password, user.password_hash):
        return None
    return user
