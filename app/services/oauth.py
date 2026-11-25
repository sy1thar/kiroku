import hashlib
import hmac
import secrets
from urllib.parse import urlencode
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import OAuthAccount, User
from app.services.auth import hash_password


def get_oauth_providers() -> list[dict]:
    s = get_settings()
    return [
        {
            "id": "google",
            "name": "Google",
            "configured": bool(s.google_client_id and s.google_client_secret),
            "env_hint": "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET",
        },
        {
            "id": "apple",
            "name": "Apple",
            "configured": bool(s.apple_client_id and s.apple_team_id),
            "env_hint": "APPLE_CLIENT_ID, APPLE_TEAM_ID",
        },
        {
            "id": "discord",
            "name": "Discord",
            "configured": bool(s.discord_client_id and s.discord_client_secret),
            "env_hint": "DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET",
        },
        {
            "id": "telegram",
            "name": "Telegram",
            "configured": bool(s.telegram_bot_username and s.telegram_bot_token),
            "env_hint": "TELEGRAM_BOT_USERNAME, TELEGRAM_BOT_TOKEN",
        },
        {
            "id": "github",
            "name": "GitHub",
            "configured": bool(s.github_client_id and s.github_client_secret),
            "env_hint": "GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET",
        },
        {
            "id": "vk",
            "name": "VK",
            "configured": bool(s.vk_client_id and s.vk_client_secret),
            "env_hint": "VK_CLIENT_ID, VK_CLIENT_SECRET",
        },
    ]


def build_oauth_authorize_url(provider: str, redirect_uri: str, state: str) -> str | None:
    s = get_settings()
    if provider == "google" and s.google_client_id:
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": s.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        })
    if provider == "discord" and s.discord_client_id:
        return "https://discord.com/api/oauth2/authorize?" + urlencode({
            "client_id": s.discord_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify email",
            "state": state,
        })
    if provider == "github" and s.github_client_id:
        return "https://github.com/login/oauth/authorize?" + urlencode({
            "client_id": s.github_client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
            "state": state,
        })
    if provider == "apple" and s.apple_client_id:
        return "https://appleid.apple.com/auth/authorize?" + urlencode({
            "client_id": s.apple_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
        })
    if provider == "vk" and s.vk_client_id:
        return "https://oauth.vk.com/authorize?" + urlencode({
            "client_id": s.vk_client_id,
            "redirect_uri": redirect_uri,
            "display": "page",
            "scope": "email",
            "response_type": "code",
            "v": "5.131",
            "state": state,
        })
    return None


async def exchange_oauth_code(provider: str, code: str, redirect_uri: str) -> tuple[str, str, str]:
    """
    Exchange authorization code for (provider_user_id, username_hint, email_hint).
    """
    s = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        if provider == "google":
            tok = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": s.google_client_id,
                    "client_secret": s.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            tok.raise_for_status()
            access = tok.json()["access_token"]
            prof = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access}"},
            )
            prof.raise_for_status()
            info = prof.json()
            return str(info["id"]), info.get("name") or "google_user", info.get("email") or ""

        if provider == "discord":
            tok = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": s.discord_client_id,
                    "client_secret": s.discord_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            tok.raise_for_status()
            access = tok.json()["access_token"]
            prof = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access}"},
            )
            prof.raise_for_status()
            info = prof.json()
            return str(info["id"]), info.get("username") or "discord_user", info.get("email") or ""

        if provider == "github":
            tok = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": s.github_client_id,
                    "client_secret": s.github_client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            tok.raise_for_status()
            access = tok.json()["access_token"]
            prof = await client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
            )
            prof.raise_for_status()
            info = prof.json()
            return str(info["id"]), info.get("login") or "github_user", info.get("email") or ""

        if provider == "vk":
            tok = await client.get(
                "https://oauth.vk.com/access_token",
                params={
                    "client_id": s.vk_client_id,
                    "client_secret": s.vk_client_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
            tok.raise_for_status()
            info = tok.json()
            uid = str(info["user_id"])
            return uid, f"vk_{uid}", info.get("email") or ""

    raise ValueError(f"Unsupported or unconfigured OAuth callback for {provider}")


def verify_telegram_login(params: dict[str, str]) -> tuple[str, str]:
    """Verify Telegram Login Widget HMAC-SHA256 signature and return (telegram_id, username)."""
    s = get_settings()
    if not s.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    check_hash = params.get("hash", "")
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k != "hash" and v
    )
    secret_key = hashlib.sha256(s.telegram_bot_token.encode("utf-8")).digest()
    computed = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, check_hash):
        raise ValueError("Invalid Telegram auth signature")
    uid = str(params["id"])
    uname = params.get("username") or params.get("first_name") or f"tg_{uid}"
    return uid, uname


def find_or_create_oauth_user(
    db: Session,
    *,
    provider: str,
    provider_user_id: str,
    username_hint: str,
    email_hint: str = "",
) -> User:
    account = db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == provider_user_id,
        )
    )
    if account:
        user = db.get(User, account.user_id)
        if user:
            return user

    clean_email = email_hint.strip().lower() or f"{provider}_{provider_user_id}@oauth.kiroku.local"
    user = db.scalar(select(User).where(User.email == clean_email))
    if not user:
        base_name = "".join(ch for ch in username_hint if ch.isalnum() or ch in "_.-")[:28] or f"{provider}_user"
        candidate = base_name
        idx = 2
        while db.scalar(select(User.id).where(User.username == candidate)) is not None:
            candidate = f"{base_name}_{idx}"
            idx += 1
        user = User(
            username=candidate,
            email=clean_email,
            password_hash=hash_password(secrets.token_urlsafe(24)),
            role="user",
        )
        db.add(user)
        db.flush()

    db.add(OAuthAccount(user_id=user.id, provider=provider, provider_user_id=provider_user_id))
    db.commit()
    db.refresh(user)
    return user
