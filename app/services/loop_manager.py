import asyncio
from dataclasses import dataclass, field
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.i18n import MEDIA_TYPE_LABELS
from app.models import Character, ExternalMapping, Media
from app.services.catalog import pick_media_translation
from app.services.importers.anilist import import_more_characters_for_media
from app.services.importers.base import clean_html_markup
from app.services.importers.batch import run_batch_import
from app.services.translation import (
    _has_cyrillic,
    fetch_shikimori_character_ru,
    fetch_shikimori_ru,
    fetch_wikipedia_ru,
    translate_text,
)


@dataclass
class LoopState:
    job_id: str = ""
    active: bool = False
    stop_requested: bool = False
    status: str = "idle"  # idle | running | stopping | completed | stopped | error
    kind: str = ""  # titles | characters_all | media_characters | translate | repair
    locale: str = "ru"
    title_hint: str = ""
    current_label: str = ""
    wave: int = 0
    added_total: int = 0
    max_items: int = 0
    progress_pct: int = 0
    media_id: int | None = None
    media_slug: str = ""
    media_exhausted: bool = False
    last_error: str = ""
    recent_logs: list[dict] = field(default_factory=list)
    last_items: list[dict] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)

    def log(self, text: str, tone: str = "info") -> None:
        self.recent_logs.insert(0, {"text": text, "tone": tone})
        if len(self.recent_logs) > 40:
            self.recent_logs = self.recent_logs[:40]

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "active": self.active,
            "stop_requested": self.stop_requested,
            "status": self.status,
            "kind": self.kind,
            "locale": self.locale,
            "title_hint": self.title_hint,
            "current_label": self.current_label,
            "wave": self.wave,
            "added_total": self.added_total,
            "max_items": self.max_items,
            "progress_pct": self.progress_pct,
            "media_id": self.media_id,
            "media_slug": self.media_slug,
            "media_exhausted": self.media_exhausted,
            "last_error": self.last_error,
            "recent_logs": self.recent_logs[:20],
            "last_items": self.last_items[:12],
            "cursors": self.cursors,
        }


_JOBS: dict[str, LoopState] = {}
_TASKS: dict[str, asyncio.Task] = {}
_DB_SESSION_PROVIDER = None
_STARTUP_REPAIR_STATUS: dict = {"done": False, "fixed_media": 0, "fixed_chars": 0, "error": ""}


def get_startup_repair_status() -> dict:
    return dict(_STARTUP_REPAIR_STATUS)


def set_loop_db_provider(provider_fn) -> None:
    """Optional override hook used in tests so background loops use the test DB."""
    global _DB_SESSION_PROVIDER
    _DB_SESSION_PROVIDER = provider_fn


def _open_session() -> tuple[Session, object | None]:
    if _DB_SESSION_PROVIDER is not None:
        gen = _DB_SESSION_PROVIDER()
        return next(gen), gen
    return SessionLocal(), None


def _close_session(db: Session, gen: object | None) -> None:
    if gen is not None:
        try:
            gen.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    else:
        db.close()


def get_loop_status() -> dict:
    jobs = [j.to_dict() for j in _JOBS.values()]
    primary = next((j for j in reversed(jobs) if j["active"]), None) or (jobs[-1] if jobs else LoopState().to_dict())
    out = dict(primary)
    out["jobs"] = jobs
    out["any_active"] = any(j["active"] for j in jobs)
    return out


def request_stop_loop(job_id: str | None = None) -> dict:
    if job_id:
        job = _JOBS.get(job_id)
        if job and job.active:
            job.stop_requested = True
            job.status = "stopping"
        return job.to_dict() if job else get_loop_status()

    for job in _JOBS.values():
        if job.active:
            job.stop_requested = True
            job.status = "stopping"
    return get_loop_status()


def dismiss_loop_job(job_id: str | None = None) -> dict:
    if job_id:
        job = _JOBS.get(job_id)
        if job and not job.active:
            _JOBS.pop(job_id, None)
            _TASKS.pop(job_id, None)
    else:
        for k in [k for k, v in _JOBS.items() if not v.active]:
            _JOBS.pop(k, None)
            _TASKS.pop(k, None)
    return get_loop_status()


async def _sleep_interruptible(state: LoopState, seconds: float) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0 and not state.stop_requested:
        step = min(0.25, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def _with_retry(state: LoopState, coro_fn):
    for attempt in range(4):
        if state.stop_requested:
            return None
        try:
            return await coro_fn()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code if exc.response is not None else 502
            if code in (429, 502, 503, 504) and attempt < 3:
                state.log(f"HTTP {code} — пауза 4 сек перед повтором...", "warn")
                await _sleep_interruptible(state, 4.0)
                continue
            raise
        except httpx.HTTPError as exc:
            if attempt < 3:
                state.log(f"Сетевая пауза ({exc}) — повтор через 4 сек...", "warn")
                await _sleep_interruptible(state, 4.0)
                continue
            raise
    return None


async def translate_missing_wave(db: Session, target_locale: str = "ru") -> tuple[int, int]:
    """Run one fast concurrent wave localizing untranslated media & characters into target_locale."""
    fixed_media = 0
    fixed_chars = 0
    if target_locale != "ru":
        return 0, 0

    media_rows = db.scalars(select(Media).options(selectinload(Media.translations))).all()
    media_tasks = []
    for m in media_rows:
        if len(media_tasks) >= 20:
            break
        if m.slug in ("elden-ring-game", "persona-5-royal-game"):
            continue
        ru_tr = next((t for t in m.translations if t.locale == "ru"), None)
        en_tr = next((t for t in m.translations if t.locale == "en"), None)
        if not ru_tr:
            continue
        needs_title = not _has_cyrillic(ru_tr.title)
        needs_syn = bool(en_tr and en_tr.synopsis and not _has_cyrillic(ru_tr.synopsis))
        if needs_title or needs_syn:
            media_tasks.append((m, ru_tr, en_tr, needs_title, needs_syn))

    sem = asyncio.Semaphore(10)

    async def _loc_m(entry):
        m, _ru_tr, en_tr, needs_title, needs_syn = entry
        is_shiki_type = m.media_type in ("anime", "manga", "manhwa", "manhua", "novel")
        kind = "animes" if m.media_type == "anime" else "mangas"
        async with sem:
            if is_shiki_type:
                ru_title, ru_desc = await fetch_shikimori_ru(
                    kind=kind,
                    search_title=m.canonical_title,
                    alt_titles=[m.canonical_title, m.native_title],
                )
            else:
                ru_title, ru_desc = "", ""
            wiki_ru = await fetch_wikipedia_ru(m.canonical_title, m.media_type) if (needs_title and not ru_title and not is_shiki_type) else ""
            chosen_title = ru_title or wiki_ru
            t_coro = translate_text(m.canonical_title, "ru") if (needs_title and not chosen_title) else asyncio.sleep(0, result=(chosen_title, "api_raw"))
            s_coro = (
                translate_text(clean_html_markup(en_tr.synopsis)[:1400], "ru")
                if (needs_syn and not (ru_desc and _has_cyrillic(ru_desc)) and en_tr and en_tr.synopsis)
                else asyncio.sleep(0, result=(ru_desc, "community_verified" if ru_desc else "api_raw"))
            )
            (rt, _), (rs, rsrc) = await asyncio.gather(t_coro, s_coro)
            return rt, rs, rsrc

    if media_tasks:
        m_results = await asyncio.gather(*[_loc_m(entry) for entry in media_tasks])
        for (m, ru_tr, _en_tr, needs_title, needs_syn), (rt, rs, rsrc) in zip(media_tasks, m_results):
            if needs_title and rt and _has_cyrillic(rt):
                ru_tr.title = rt
            if needs_syn and rs and _has_cyrillic(rs):
                ru_tr.synopsis = rs
                ru_tr.source = rsrc
            fixed_media += 1

    char_rows = db.scalars(select(Character).options(selectinload(Character.translations))).all()
    char_tasks = []
    for c in char_rows:
        if len(char_tasks) >= 30:
            break
        ru_tr = next((t for t in c.translations if t.locale == "ru"), None)
        en_tr = next((t for t in c.translations if t.locale == "en"), None)
        if not ru_tr:
            continue
        needs_name = not _has_cyrillic(ru_tr.name)
        needs_bio = bool(en_tr and en_tr.bio and not _has_cyrillic(ru_tr.bio))
        if needs_name or needs_bio:
            char_tasks.append((c, ru_tr, en_tr, needs_name, needs_bio))

    async def _loc_c(entry):
        c, _ru_tr, en_tr, needs_name, needs_bio = entry
        async with sem:
            rn = await fetch_shikimori_character_ru(c.canonical_name) if needs_name else ""
            n_coro = translate_text(c.canonical_name, "ru") if (needs_name and not rn) else asyncio.sleep(0, result=(rn, "api_raw"))
            b_coro = (
                translate_text(clean_html_markup(en_tr.bio)[:900], "ru")
                if (needs_bio and en_tr and en_tr.bio)
                else asyncio.sleep(0, result=("", "api_raw"))
            )
            (name_res, _), (bio_res, bsrc) = await asyncio.gather(n_coro, b_coro)
            return name_res, bio_res, bsrc

    if char_tasks:
        c_results = await asyncio.gather(*[_loc_c(entry) for entry in char_tasks])
        for (_c, ru_tr, _en_tr, needs_name, needs_bio), (nr, br, bsrc) in zip(char_tasks, c_results):
            if needs_name and nr and _has_cyrillic(nr):
                ru_tr.name = nr
            if needs_bio and br and _has_cyrillic(br):
                ru_tr.bio = br
                ru_tr.source = bsrc
            fixed_chars += 1

    db.commit()
    return fixed_media, fixed_chars


async def _run_titles_loop(state: LoopState, categories: list[str], batch_size: int, max_items: int, delay_ms: int, locale: str) -> None:
    cats = list(categories) if categories else list(MEDIA_TYPE_LABELS.keys())
    active_cats = list(cats)
    empty_streaks = {c: 0 for c in active_cats}
    total_cats = len(active_cats)

    while active_cats and not state.stop_requested:
        for cat in list(active_cats):
            if state.stop_requested:
                break
            if max_items > 0 and state.added_total >= max_items:
                state.stop_requested = True
                break

            label = MEDIA_TYPE_LABELS.get(cat, {}).get(locale, cat)
            state.wave += 1
            state.current_label = f"{label}"

            async def _step(c_key: str = cat):
                db, gen = _open_session()
                try:
                    return await run_batch_import(db, category=c_key, count=batch_size, locale=locale)
                finally:
                    _close_session(db, gen)

            res = await _with_retry(state, _step)
            if res is None or state.stop_requested:
                break

            added = int(res.get("imported_count") or 0)
            state.added_total += added
            state.cursors[cat] = int(res.get("next_page") or 1)
            state.current_label = f"{label} · {'стр.' if locale == 'ru' else 'p.'} {res.get('page_imported')}"
            if res.get("items"):
                state.last_items = res["items"]
            empty_streaks[cat] = 0 if added > 0 else empty_streaks[cat] + 1
            state.log(
                f"[{label} · {'стр.' if locale == 'ru' else 'p.'} {res.get('page_imported')}] +{added}",
                "ok" if added > 0 else "muted",
            )

            if not res.get("has_next_page") or empty_streaks[cat] >= 20:
                state.log(f"[{label}] {'категория завершена' if locale == 'ru' else 'finished'}", "info")
                active_cats.remove(cat)

            if max_items > 0:
                state.progress_pct = min(100, int((state.added_total / max_items) * 100))
            elif total_cats > 0:
                done_cats = total_cats - len(active_cats)
                state.progress_pct = min(99, max(5, int((done_cats / total_cats) * 100) or min(90, state.wave * 4)))

            if delay_ms > 0 and not state.stop_requested:
                await _sleep_interruptible(state, delay_ms / 1000.0)


async def _run_characters_loop(
    state: LoopState,
    categories: list[str],
    media_id: int | None,
    batch_size: int,
    max_items: int,
    delay_ms: int,
    locale: str,
) -> None:
    db, gen = _open_session()
    try:
        stmt = (
            select(Media)
            .join(
                ExternalMapping,
                (ExternalMapping.entity_type == "media")
                & (ExternalMapping.entity_id == Media.id)
                & (ExternalMapping.provider.in_(["anilist", "vndb"])),
            )
            .where(Media.characters_exhausted.is_(False))
            .options(selectinload(Media.translations))
            .order_by(Media.popularity.desc(), Media.id.asc())
        )
        if media_id is not None:
            stmt = stmt.where(Media.id == media_id)
        elif categories:
            stmt = stmt.where(Media.media_type.in_(categories))
        targets = []
        for m in db.scalars(stmt).all():
            tr = pick_media_translation(m, locale)
            targets.append((m.id, m.slug, tr.title if tr else m.canonical_title))
    finally:
        _close_session(db, gen)

    if not targets:
        state.media_exhausted = True if media_id is not None else False
        state.progress_pct = 100
        state.log("Нет тайтлов с недогруженными персонажами." if locale == "ru" else "No titles remaining.", "info")
        return

    if media_id is not None and targets:
        state.title_hint = targets[0][2]
        state.media_slug = targets[0][1]

    for idx, (mid, mslug, mtitle) in enumerate(targets):
        if state.stop_requested:
            break
        state.media_id = mid
        state.media_slug = mslug
        next_page = 1
        empty_streak = 0
        while not state.stop_requested:
            if max_items > 0 and state.added_total >= max_items:
                state.stop_requested = True
                break
            state.wave += 1
            state.current_label = f"{mtitle} · {'стр.' if locale == 'ru' else 'p.'} {next_page}"

            async def _step(target_id: int = mid, cur_p: int = next_page):
                sdb, sgen = _open_session()
                try:
                    return await import_more_characters_for_media(
                        sdb,
                        target_id,
                        count=batch_size,
                        locale=locale,
                        start_page=cur_p,
                    )
                finally:
                    _close_session(sdb, sgen)

            res = await _with_retry(state, _step)
            if res is None or state.stop_requested:
                break
            added = int(res.get("added_count") or 0)
            state.added_total += added
            next_page = int(res.get("next_page") or (next_page + 1))
            if res.get("characters"):
                state.last_items = [
                    {"title": c["name"], "slug": c["slug"], "media_type": "character", "characters_linked": 1}
                    for c in res["characters"]
                ]
            if res.get("media_exhausted"):
                state.media_exhausted = True
            empty_streak = 0 if added > 0 else empty_streak + 1
            state.log(f"[{mtitle}] +{added} перс.", "ok" if added > 0 else "muted")

            if max_items > 0:
                state.progress_pct = min(100, int((state.added_total / max_items) * 100))
            else:
                state.progress_pct = int(((idx + (0.5 if res.get("has_more") else 1.0)) / len(targets)) * 100)

            if not res.get("has_more") or empty_streak >= 3:
                break
            if delay_ms > 0:
                await _sleep_interruptible(state, delay_ms / 1000.0)
        if not state.stop_requested:
            state.progress_pct = int(((idx + 1) / len(targets)) * 100)


async def _run_translate_loop(state: LoopState, locale: str, delay_ms: int) -> None:
    while not state.stop_requested:
        state.wave += 1
        state.current_label = f"{'Волна перевода' if locale == 'ru' else 'Translate wave'} #{state.wave}"
        db, gen = _open_session()
        try:
            m_cnt, c_cnt = await translate_missing_wave(db, target_locale=locale)
        finally:
            _close_session(db, gen)
        state.added_total += m_cnt + c_cnt
        state.log(f"Волна #{state.wave}: тайтлов +{m_cnt}, персов +{c_cnt}", "ok" if (m_cnt + c_cnt) else "muted")
        if m_cnt == 0 and c_cnt == 0:
            state.progress_pct = 100
            break
        if delay_ms > 0:
            await _sleep_interruptible(state, delay_ms / 1000.0)


async def repair_translations_wave(db: Session, offset: int = 0, limit: int = 25) -> tuple[int, int]:
    """Scan existing media rows (newest first so newly broken items repair immediately) and fix wrong/EN titles."""
    rows = list(
        db.scalars(
            select(Media)
            .options(selectinload(Media.translations))
            .order_by(Media.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    if not rows:
        return 0, 0

    sem = asyncio.Semaphore(8)

    async def _check_fix(m: Media):
        if m.slug in ("elden-ring-game", "persona-5-royal-game"):
            return None
        ru_tr = next((t for t in m.translations if t.locale == "ru"), None)
        en_tr = next((t for t in m.translations if t.locale == "en"), None)
        if not ru_tr:
            return None
        is_shiki_type = m.media_type in ("anime", "manga", "manhwa", "manhua", "novel")
        has_cy = _has_cyrillic(ru_tr.title)
        absurd_shiki = len(ru_tr.title or "") > (len(m.canonical_title or "") * 2.3 + 25)
        needs_syn = bool(en_tr and en_tr.synopsis and not _has_cyrillic(ru_tr.synopsis))
        should_verify_shiki = is_shiki_type and (
            (not has_cy)
            or (m.media_type in ("manga", "manhwa", "manhua", "novel"))
            or (ru_tr.source == "community_verified")
            or absurd_shiki
        )
        if has_cy and not should_verify_shiki and not needs_syn:
            return None

        async with sem:
            shiki_title, shiki_desc = ("", "")
            if should_verify_shiki:
                kind = "animes" if m.media_type == "anime" else "mangas"
                shiki_title, shiki_desc = await fetch_shikimori_ru(
                    kind=kind,
                    search_title=m.canonical_title,
                    alt_titles=[m.canonical_title, m.native_title],
                )
            if shiki_title and _has_cyrillic(shiki_title):
                new_t = shiki_title
                new_s = shiki_desc if (shiki_desc and _has_cyrillic(shiki_desc)) else ru_tr.synopsis
                if not _has_cyrillic(new_s) and en_tr and en_tr.synopsis:
                    new_s, _ = await translate_text(clean_html_markup(en_tr.synopsis)[:1400], "ru")
                return (m, ru_tr, new_t, new_s, "community_verified")

            needs_repair = (not has_cy) or (ru_tr.source == "community_verified" and is_shiki_type) or absurd_shiki
            if not needs_repair and not needs_syn:
                return None

            wiki_ru = await fetch_wikipedia_ru(m.canonical_title, m.media_type) if (needs_repair and not is_shiki_type) else ""
            t_coro = asyncio.sleep(0, result=(wiki_ru, "community_verified")) if wiki_ru else (
                translate_text(m.canonical_title, "ru") if needs_repair else asyncio.sleep(0, result=(ru_tr.title, ru_tr.source))
            )
            s_coro = (
                translate_text(clean_html_markup(en_tr.synopsis)[:1400], "ru")
                if (needs_syn and en_tr and en_tr.synopsis)
                else asyncio.sleep(0, result=(ru_tr.synopsis, ru_tr.source))
            )
            (nt, _), (ns, nsrc) = await asyncio.gather(t_coro, s_coro)
            return (m, ru_tr, nt, ns, nsrc)

    fixes = await asyncio.gather(*[_check_fix(m) for m in rows])
    changed = 0
    for fx in fixes:
        if not fx:
            continue
        _m, ru_tr, new_t, new_s, new_src = fx
        updated = False
        if new_t and _has_cyrillic(new_t) and new_t != ru_tr.title:
            ru_tr.title = new_t
            updated = True
        if new_s and _has_cyrillic(new_s) and new_s != ru_tr.synopsis:
            ru_tr.synopsis = new_s
            ru_tr.source = new_src
            updated = True
        if updated:
            changed += 1

    if changed:
        db.commit()
    return len(rows), changed


async def _run_repair_loop(state: LoopState, locale: str, delay_ms: int) -> None:
    offset = 0
    batch = 25
    while not state.stop_requested:
        state.wave += 1
        state.current_label = f"Проверка и починка #{state.wave} (с {offset})"
        db, gen = _open_session()
        try:
            scanned, fixed = await repair_translations_wave(db, offset=offset, limit=batch)
        finally:
            _close_session(db, gen)
        state.added_total += fixed
        state.log(f"Волна #{state.wave}: проверено {scanned}, исправлено {fixed}", "ok" if fixed else "muted")
        if scanned < batch:
            break
        offset += batch
        if delay_ms > 0:
            await _sleep_interruptible(state, delay_ms / 1000.0)
    await _run_translate_loop(state, locale, delay_ms)


async def run_startup_repair() -> None:
    """Fast background pass executed on production boot to fix corrupted & untranslated DB rows immediately."""
    await asyncio.sleep(1.0)
    offset = 0
    batch = 25
    try:
        while True:
            db, gen = _open_session()
            try:
                scanned, fixed = await repair_translations_wave(db, offset=offset, limit=batch)
                _STARTUP_REPAIR_STATUS["fixed_media"] += fixed
            finally:
                _close_session(db, gen)
            if scanned < batch:
                break
            offset += batch
            await asyncio.sleep(0.2)

        for _ in range(40):
            db, gen = _open_session()
            try:
                m_cnt, c_cnt = await translate_missing_wave(db, target_locale="ru")
                _STARTUP_REPAIR_STATUS["fixed_media"] += m_cnt
                _STARTUP_REPAIR_STATUS["fixed_chars"] += c_cnt
            finally:
                _close_session(db, gen)
            if m_cnt == 0 and c_cnt == 0:
                break
            await asyncio.sleep(0.2)
    except Exception as exc:
        _STARTUP_REPAIR_STATUS["error"] = str(exc)
    finally:
        _STARTUP_REPAIR_STATUS["done"] = True


async def _execute_job(
    state: LoopState,
    kind: str,
    categories: list[str],
    media_id: int | None,
    batch_size: int,
    max_items: int,
    delay_ms: int,
    locale: str,
) -> None:
    try:
        if kind == "titles":
            await _run_titles_loop(state, categories, batch_size, max_items, delay_ms, locale)
        elif kind in ("characters_all", "media_characters"):
            await _run_characters_loop(state, categories, media_id, batch_size, max_items, delay_ms, locale)
        elif kind == "translate":
            await _run_translate_loop(state, locale, delay_ms)
        elif kind == "repair":
            await _run_repair_loop(state, locale, delay_ms)
        state.status = "stopped" if state.stop_requested else "completed"
        if state.status == "completed":
            state.progress_pct = 100
    except Exception as exc:
        state.status = "error"
        state.last_error = str(exc)
        state.log(f"Ошибка цикла: {exc}", "err")
    finally:
        state.active = False


def start_background_loop(
    *,
    kind: str,
    categories: list[str],
    media_id: int | None,
    batch_size: int,
    max_items: int,
    delay_ms: int,
    locale: str,
) -> dict:
    if kind == "media_characters" and media_id is not None:
        job_id = f"media_characters:{media_id}"
    else:
        job_id = kind

    existing = _JOBS.get(job_id)
    if existing and existing.active:
        raise ValueError("This loop is already running.")

    state = LoopState(
        job_id=job_id,
        active=True,
        stop_requested=False,
        status="running",
        kind=kind,
        locale=locale,
        current_label="Запуск..." if locale == "ru" else "Starting...",
        wave=0,
        added_total=0,
        max_items=max_items,
        progress_pct=0,
        media_id=media_id,
    )
    _JOBS[job_id] = state
    _TASKS[job_id] = asyncio.create_task(
        _execute_job(state, kind, categories, media_id, batch_size, max_items, delay_ms, locale)
    )
    return state.to_dict()
