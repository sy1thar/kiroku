import asyncio
import re
import weakref
import httpx

from app.config import get_settings

_SHIKI_HEADERS = {"User-Agent": "KirokuApp/1.0 (https://kiroku.local)"}
_LOOP_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
_LOOP_SEMS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_TEXT_CACHE: dict[tuple[str, str], tuple[str, str]] = {}
_SHIKI_MEDIA_CACHE: dict[tuple[str, int | None, str], tuple[str, str]] = {}
_SHIKI_CHAR_CACHE: dict[str, str] = {}
_MAX_CACHE_SIZE = 2500


def _get_pooled_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _LOOP_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=6.0,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )
        _LOOP_CLIENTS[loop] = client
    return client


def _get_shiki_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _LOOP_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(6)
        _LOOP_SEMS[loop] = sem
    return sem


def _cache_put(cache: dict, key, value) -> None:
    if len(cache) >= _MAX_CACHE_SIZE:
        cache.clear()
    cache[key] = value


def _clean_shikimori_bbcode(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\[/?[a-zA-Z0-9_]+(?:=[^\]]*)?\]", "", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[а-яА-ЯёЁ]", text or ""))


async def _shiki_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> httpx.Response:
    async with _get_shiki_sem():
        resp = await client.get(url, params=params, headers=_SHIKI_HEADERS)
        if resp.status_code == 429:
            await asyncio.sleep(0.5)
            resp = await client.get(url, params=params, headers=_SHIKI_HEADERS)
        return resp


async def fetch_shikimori_ru(
    *,
    kind: str,
    mal_id: int | None = None,
    search_title: str = "",
) -> tuple[str, str]:
    """
    Fetch canonical Russian title and synopsis from Shikimori API using pooled HTTP connections.
    """
    endpoint_kind = "animes" if kind == "animes" else "mangas"
    cache_key = (endpoint_kind, mal_id, search_title.strip().lower())
    if cache_key in _SHIKI_MEDIA_CACHE:
        return _SHIKI_MEDIA_CACHE[cache_key]
    try:
        client = _get_pooled_client()
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
            res = (ru_title, ru_desc)
            _cache_put(_SHIKI_MEDIA_CACHE, cache_key, res)
            return res
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    _cache_put(_SHIKI_MEDIA_CACHE, cache_key, ("", ""))
    return "", ""


async def fetch_shikimori_character_ru(name: str) -> str:
    """Fetch canonical Russian character name from Shikimori API using pooled HTTP connections."""
    clean_name = name.strip()
    if not clean_name:
        return ""
    cache_key = clean_name.lower()
    if cache_key in _SHIKI_CHAR_CACHE:
        return _SHIKI_CHAR_CACHE[cache_key]
    try:
        client = _get_pooled_client()
        resp = await _shiki_get(
            client,
            "https://shikimori.io/api/characters/search",
            params={"search": clean_name},
        )
        if resp.status_code == 200 and isinstance(resp.json(), list) and resp.json():
            best = resp.json()[0]
            ru_name = (best.get("russian") or "").strip()
            if ru_name:
                _cache_put(_SHIKI_CHAR_CACHE, cache_key, ru_name)
                return ru_name
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    _cache_put(_SHIKI_CHAR_CACHE, cache_key, "")
    return ""


async def translate_text(text: str, target_locale: str) -> tuple[str, str]:
    """
    Translate text to target_locale using DeepL, OpenAI, Google GTX, or MyMemory fallback with pooling & caching.
    """
    clean_text = text.strip()
    if not clean_text:
        return "", "api_raw"

    settings = get_settings()
    if target_locale == "en":
        return text, "api_raw"

    cache_key = (target_locale, clean_text[:900])
    if cache_key in _TEXT_CACHE:
        return _TEXT_CACHE[cache_key]

    client = _get_pooled_client()

    if settings.deepl_api_key:
        endpoint = (
            "https://api-free.deepl.com/v2/translate"
            if settings.deepl_api_key.endswith(":fx")
            else "https://api.deepl.com/v2/translate"
        )
        try:
            resp = await client.post(
                endpoint,
                headers={"Authorization": f"DeepL-Auth-Key {settings.deepl_api_key}"},
                data={"text": text, "target_lang": target_locale.upper()},
                timeout=10.0,
            )
            if resp.status_code == 200:
                payload = resp.json()
                translations = payload.get("translations") or []
                if translations:
                    out = (translations[0]["text"], "ai_draft")
                    _cache_put(_TEXT_CACHE, cache_key, out)
                    return out
        except httpx.HTTPError:
            pass

    if settings.openai_api_key:
        try:
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
                timeout=12.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    out = (content, "ai_draft")
                    _cache_put(_TEXT_CACHE, cache_key, out)
                    return out
        except (httpx.HTTPError, KeyError, IndexError):
            pass

    # Free GTX translation fallback
    try:
        snippet = clean_text[:1800]
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
                out = (translated, "ai_draft")
                _cache_put(_TEXT_CACHE, cache_key, out)
                return out
    except (httpx.HTTPError, ValueError, TypeError, IndexError):
        pass

    # Free MyMemory translation fallback (useful if GTX rate-limits shared cloud IPs)
    try:
        snippet = clean_text[:480]
        resp = await client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": snippet, "langpair": f"en|{target_locale}"},
        )
        if resp.status_code == 200:
            translated = ((resp.json().get("responseData") or {}).get("translatedText") or "").strip()
            if translated and (target_locale != "ru" or _has_cyrillic(translated)):
                out = (translated, "ai_draft")
                _cache_put(_TEXT_CACHE, cache_key, out)
                return out
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        pass

    return text, "api_raw"
