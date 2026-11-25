# Kiroku (記録) — Global Cross-Media & Character Encyclopedia

Единый агрегатор и вики-энциклопедия по аниме, манге, манхве, видеоиграм и визуальным новеллам с кросс-медиа графом персонажей, поиском по внешности/психотипам, защитой от спойлеров и локализацией (RU / EN).

## Реализованные возможности
- **Кросс-медиа модель данных** (`app/models.py`):
  - `Franchise` → `Media` (`anime`, `manga`, `manhwa`, `game`, `vn`, `novel`) → `MediaTranslation` (`ru`, `en`, статус `api_raw | ai_draft | community_verified`).
  - `Character` + `CharacterTranslation` + `MediaCharacter` (один персонаж без дублей во всех адаптациях и играх).
  - `Trait` + `CharacterTrait` (теги внешности, способностей и характера с голосованием).
  - `ExternalMapping` (`anilist`, `vndb`, `igdb`, `wikidata`) для дедупликации и синхронизации.
  - `Revision` — очередь вики-правок с премодерацией (Approve / Reject).
  - `UserListEntry` — личный трекер прогресса и оценок **Kiroku**.
- **Сквозной кросс-языковой поиск** (`app/services/search.py`):
  - Поддержка **Meilisearch** (при задании `MEILISEARCH_URL`) и встроенный fuzzy-поиск с транслитерацией (`Годжо` ↔ `Gojo`).
- **Импортеры внешних API** (`app/services/importers/`):
  - **AniList GraphQL** (аниме, манга, манхва, ранобэ + персонажи и сэйю).
  - **VNDB Kana API v2** (визуальные новеллы + персонажи).
  - **IGDB API** (видеоигры + персонажи через Twitch OAuth).
- **SEO-дружелюбный SSR** (FastAPI + Jinja2, маршруты `/ru/...` и `/en/...`, `hreflang`).

## Быстрый старт локально

```powershell
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

После запуска откройте:
- Веб-интерфейс: `http://127.0.0.1:8000/ru`
- Swagger API: `http://127.0.0.1:8000/docs`

## Запуск тестов

```powershell
.venv\Scripts\pytest -v
```

## Продакшен / Глобальный стек (Docker Compose)

Скопируйте `.env.example` в `.env`, заполните ключи (`TWITCH_CLIENT_ID`, `DEEPL_API_KEY`/`OPENAI_API_KEY`, `R2_*`) и запустите:

```bash
docker compose up --build
```
