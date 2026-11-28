import asyncio
from difflib import SequenceMatcher
import re
import weakref
import httpx

from app.config import get_settings

_SHIKI_HEADERS = {"User-Agent": "KirokuApp/1.0 (https://kiroku.local)"}
_LOOP_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
_LOOP_SEMS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_LOOP_TR_SEMS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
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
        sem = asyncio.Semaphore(3)
        _LOOP_SEMS[loop] = sem
    return sem


def _get_tr_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _LOOP_TR_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(4)
        _LOOP_TR_SEMS[loop] = sem
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


def _norm_ascii(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _title_similarity(candidates: list[str], item: dict) -> float:
    item_names = [item.get("name") or "", item.get("english") or ""] + list(item.get("synonyms") or [])
    norm_cands = [_norm_ascii(c) for c in candidates if _norm_ascii(c)]
    norm_items = [_norm_ascii(n) for n in item_names if _norm_ascii(n)]
    if not norm_cands or not norm_items:
        return 0.0
    best = 0.0
    for q in norm_cands:
        for s in norm_items:
            if q == s:
                return 1.0
            ratio = SequenceMatcher(None, q, s).ratio()
            if ratio > best:
                best = ratio
    return best


def _character_name_matches(query_name: str, shiki_name: str) -> bool:
    q = _norm_ascii(query_name)
    s = _norm_ascii(shiki_name)
    if not q or not s:
        return False
    if q == s:
        return True
    if sorted(q.split()) == sorted(s.split()):
        return True
    return SequenceMatcher(None, q, s).ratio() >= 0.78


async def _shiki_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> httpx.Response:
    async with _get_shiki_sem():
        resp = await client.get(url, params=params, headers=_SHIKI_HEADERS)
        if resp.status_code == 429:
            await asyncio.sleep(0.8)
            resp = await client.get(url, params=params, headers=_SHIKI_HEADERS)
        if resp.status_code == 429:
            await asyncio.sleep(1.6)
            resp = await client.get(url, params=params, headers=_SHIKI_HEADERS)
        return resp


_WIKI_UA = {"User-Agent": "KirokuEncyclopediaBot/1.0 (https://kiroku-w1mf.onrender.com)"}
_WIKI_CACHE: dict[tuple[str, str], str] = {}


def _clean_wiki_ru_title(raw: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", (raw or "").strip()).strip()


async def fetch_wikipedia_ru(title: str, media_type: str = "") -> str:
    """Resolve official Russian title via English Wikipedia langlinks (no API key, cloud-friendly)."""
    clean = (title or "").strip()
    if not clean:
        return ""
    key = (clean.lower(), media_type)
    if key in _WIKI_CACHE:
        return _WIKI_CACHE[key]

    suffixes = {
        "series": [f"{clean} (American TV series)", f"{clean} (TV series)", clean],
        "game": [f"{clean} (video game)", clean],
        "vn": [f"{clean} (visual novel)", clean],
    }.get(media_type, [clean])

    try:
        client = _get_pooled_client()
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": "|".join(suffixes),
                "prop": "langlinks",
                "lllang": "ru",
                "redirects": 1,
            },
            headers=_WIKI_UA,
        )
        if resp.status_code == 200:
            pages = ((resp.json().get("query") or {}).get("pages") or {}).values()
            # Prefer qualified page title order (e.g., "(TV series)" over generic word article)
            by_title: dict[str, str] = {}
            for page in pages:
                ll = page.get("langlinks") or []
                if ll and page.get("title"):
                    cleaned_ru = _clean_wiki_ru_title(ll[0].get("*") or "")
                    if cleaned_ru and _has_cyrillic(cleaned_ru):
                        by_title[page["title"]] = cleaned_ru
            for cand in suffixes:
                if cand in by_title:
                    _cache_put(_WIKI_CACHE, key, by_title[cand])
                    return by_title[cand]
            if by_title:
                first = next(iter(by_title.values()))
                _cache_put(_WIKI_CACHE, key, first)
                return first
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    _cache_put(_WIKI_CACHE, key, "")
    return ""


async def fetch_shikimori_ru(
    *,
    kind: str,
    mal_id: int | None = None,
    search_title: str = "",
    alt_titles: list[str] | None = None,
) -> tuple[str, str]:
    """
    Fetch canonical Russian title and synopsis from Shikimori API with strict title-similarity verification.
    """
    endpoint_kind = "animes" if kind == "animes" else "mangas"
    candidates = [t for t in ([search_title] + list(alt_titles or [])) if t and t.strip()]
    cache_key = (endpoint_kind, mal_id, search_title.strip().lower())
    if cache_key in _SHIKI_MEDIA_CACHE:
        return _SHIKI_MEDIA_CACHE[cache_key]
    try:
        client = _get_pooled_client()
        item: dict | None = None
        if mal_id:
            resp = await _shiki_get(client, f"https://shikimori.io/api/{endpoint_kind}/{mal_id}")
            if resp.status_code == 200:
                node = resp.json()
                if isinstance(node, dict) and node.get("russian"):
                    item = node
        if not item and candidates:
            for query_term in candidates[:2]:
                resp = await _shiki_get(
                    client,
                    f"https://shikimori.io/api/{endpoint_kind}",
                    params={"search": query_term, "limit": 5},
                )
                if resp.status_code == 200 and isinstance(resp.json(), list):
                    best_score = 0.0
                    best_row: dict | None = None
                    for row in resp.json():
                        if not (row.get("russian") or "").strip():
                            continue
                        score = _title_similarity(candidates, row)
                        if score >= 0.78 and score > best_score:
                            best_score = score
                            best_row = row
                    if best_row:
                        item = best_row
                        break
        if item:
            ru_title = (item.get("russian") or "").strip()
            ru_desc = _clean_shikimori_bbcode(item.get("description"))
            if not ru_desc and item.get("id"):
                det = await _shiki_get(client, f"https://shikimori.io/api/{endpoint_kind}/{item['id']}")
                if det.status_code == 200:
                    ru_desc = _clean_shikimori_bbcode(det.json().get("description"))
            res = (ru_title, ru_desc)
            _cache_put(_SHIKI_MEDIA_CACHE, cache_key, res)
            return res
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    _cache_put(_SHIKI_MEDIA_CACHE, cache_key, ("", ""))
    return "", ""


async def fetch_shikimori_character_ru(name: str) -> str:
    """Fetch canonical Russian character name from Shikimori API with strict name verification."""
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
        if resp.status_code == 200 and isinstance(resp.json(), list):
            for row in resp.json()[:5]:
                ru_name = (row.get("russian") or "").strip()
                if ru_name and _character_name_matches(clean_name, row.get("name") or ""):
                    _cache_put(_SHIKI_CHAR_CACHE, cache_key, ru_name)
                    return ru_name
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    _cache_put(_SHIKI_CHAR_CACHE, cache_key, "")
    return ""


async def translate_text(text: str, target_locale: str) -> tuple[str, str]:
    """
    Translate text to target_locale using DeepL, OpenAI, or resilient multi-mirror free translators.
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

    is_mostly_ascii = bool(re.match(r"^[\x00-\x7F]+$", clean_text[:200]))
    src_lang = "en" if is_mostly_ascii else "auto"

    def _polish_case(src: str, out_txt: str) -> str:
        s = out_txt.strip()
        if s and src and src[0].isupper() and s[0].islower():
            return s[0].upper() + s[1:]
        return s

    async with _get_tr_sem():
        # Mirror 1: Google GTX (with 429 backoff retries)
        try:
            snippet = clean_text[:1800]
            for attempt in range(3):
                resp = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx",
                        "sl": src_lang,
                        "tl": target_locale,
                        "dt": "t",
                        "q": snippet,
                    },
                )
                if resp.status_code == 429 and attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    chunks = [seg[0] for seg in (data[0] or []) if seg and seg[0]]
                    translated = _polish_case(clean_text, "".join(chunks))
                    if translated and (target_locale != "ru" or _has_cyrillic(translated)):
                        out = (translated, "ai_draft")
                        _cache_put(_TEXT_CACHE, cache_key, out)
                        return out
                break
        except (httpx.HTTPError, ValueError, TypeError, IndexError):
            pass

        # Mirror 2: Mozhi proxy (bypasses Render shared IP blocks on Google)
        for mozhi_eng in ("google", "duckduckgo"):
            try:
                snippet = clean_text[:1200]
                resp = await client.get(
                    "https://mozhi.adminforge.de/api/translate",
                    params={"engine": mozhi_eng, "from": src_lang, "to": target_locale, "text": snippet},
                )
                if resp.status_code == 200:
                    translated = _polish_case(clean_text, (resp.json().get("translated-text") or ""))
                    if translated and (target_locale != "ru" or _has_cyrillic(translated)):
                        out = (translated, "ai_draft")
                        _cache_put(_TEXT_CACHE, cache_key, out)
                        return out
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                pass

        # Mirror 3: Chrome dict endpoint (clients5.google.com)
        try:
            snippet = clean_text[:1500]
            resp = await client.get(
                "https://clients5.google.com/translate_a/t",
                params={"client": "dict-chrome-ex", "sl": src_lang, "tl": target_locale, "q": snippet},
            )
            if resp.status_code == 200:
                payload = resp.json()
                translated = ""
                if isinstance(payload, list) and payload:
                    first = payload[0]
                    translated = first[0] if isinstance(first, list) else str(first)
                translated = _polish_case(clean_text, translated)
                if translated and (target_locale != "ru" or _has_cyrillic(translated)):
                    out = (translated, "ai_draft")
                    _cache_put(_TEXT_CACHE, cache_key, out)
                    return out
        except (httpx.HTTPError, ValueError, TypeError, IndexError):
            pass

        # Mirror 4: MyMemory with dedicated contact email (50k chars/day quota separate from cloud IP)
        try:
            snippet = clean_text[:480]
            resp = await client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": snippet, "langpair": f"en|{target_locale}", "de": "kiroku.enc@gmail.com"},
            )
            if resp.status_code == 200:
                translated = _polish_case(
                    clean_text,
                    (resp.json().get("responseData") or {}).get("translatedText") or "",
                )
                if translated and "MYMEMORY WARNING" not in translated and (target_locale != "ru" or _has_cyrillic(translated)):
                    out = (translated, "ai_draft")
                    _cache_put(_TEXT_CACHE, cache_key, out)
                    return out
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            pass

    return text, "api_raw"
