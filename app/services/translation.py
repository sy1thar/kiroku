import asyncio
import re
import httpx

from app.config import get_settings

_SHIKI_HEADERS = {"User-Agent": "KirokuApp/1.0 (https://kiroku.local)"}
_SHIKI_SEM = asyncio.Semaphore(2)


def _clean_shikimori_bbcode(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\[/?[a-zA-Z0-9_]+(?:=[^\]]*)?\]", "", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text or ""))


async def _shiki_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> httpx.Response:
    async with _SHIKI_SEM:
        resp = await client.get(url, params=params)
        if resp.status_code == 429:
            await asyncio.sleep(0.8)
            resp = await client.get(url, params=params)
        return resp


async def fetch_shikimori_ru(
    *,
    kind: str,
    mal_id: int | None = None,
    search_title: str = "",
) -> tuple[str, str]:
    """
    Fetch canonical Russian title and synopsis from Shikimori API with rate-limit retry.
    """
    endpoint_kind = "animes" if kind == "animes" else "mangas"
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers=_SHIKI_HEADERS) as client:
            item: dict | None = None
            if mal_id:
                resp = await _shiki_get(client, f"https://shikimori.io/api/{endpoint_kind}/{mal_id}")
                if resp.status_code == 200:
                    item = resp.json()
            if not item and search_title:
                resp = await _shiki_get(
                    client,
                    f"https://shikimori.io/api/{endpoint_kind}",
                    params={"search": search_title, "limit": 1},
                )
                if resp.status_code == 200 and isinstance(resp.json(), list) and resp.json():
                    item = resp.json()[0]
            if item:
                ru_title = (item.get("russian") or "").strip()
                ru_desc = _clean_shikimori_bbcode(item.get("description"))
                return ru_title, ru_desc
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    return "", ""


async def fetch_shikimori_character_ru(name: str) -> str:
    """Fetch canonical Russian character name from Shikimori API with rate-limit retry."""
    if not name.strip():
        return ""
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers=_SHIKI_HEADERS) as client:
            resp = await _shiki_get(
                client,
                "https://shikimori.io/api/characters/search",
                params={"search": name},
            )
            if resp.status_code == 200 and isinstance(resp.json(), list) and resp.json():
                best = resp.json()[0]
                ru_name = (best.get("russian") or "").strip()
                if ru_name:
                    return ru_name
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    return ""


async def translate_text(text: str, target_locale: str) -> tuple[str, str]:
    """
    Translate text to target_locale using DeepL, OpenAI, Google GTX, or MyMemory fallback.
    """
    if not text.strip():
        return "", "api_raw"

    settings = get_settings()
    if target_locale == "en":
        return text, "api_raw"

    if settings.deepl_api_key:
        endpoint = (
            "https://api-free.deepl.com/v2/translate"
            if settings.deepl_api_key.endswith(":fx")
            else "https://api.deepl.com/v2/translate"
        )
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"DeepL-Auth-Key {settings.deepl_api_key}"},
                    data={"text": text, "target_lang": target_locale.upper()},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    translations = payload.get("translations") or []
                    if translations:
                        return translations[0]["text"], "ai_draft"
        except httpx.HTTPError:
            pass

    if settings.openai_api_key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    f"Translate this anime/game/character text naturally into "
                                    f"'{target_locale}' preserving proper nouns. Return only the translation."
                                ),
                            },
                            {"role": "user", "content": text},
                        ],
                        "temperature": 0.2,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    if content:
                        return content, "ai_draft"
        except (httpx.HTTPError, KeyError, IndexError):
            pass

    # Free GTX translation fallback
    try:
        snippet = text[:2600]
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": "auto",
                    "tl": target_locale,
                    "dt": "t",
                    "q": snippet,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                chunks = [seg[0] for seg in (data[0] or []) if seg and seg[0]]
                translated = "".join(chunks).strip()
                if translated and (target_locale != "ru" or _has_cyrillic(translated)):
                    return translated, "ai_draft"
    except (httpx.HTTPError, ValueError, TypeError, IndexError):
        pass

    # Free MyMemory translation fallback (useful if GTX rate-limits shared cloud IPs)
    try:
        snippet = text[:480]
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": snippet, "langpair": f"en|{target_locale}"},
            )
            if resp.status_code == 200:
                translated = ((resp.json().get("responseData") or {}).get("translatedText") or "").strip()
                if translated and (target_locale != "ru" or _has_cyrillic(translated)):
                    return translated, "ai_draft"
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        pass

    return text, "api_raw"
