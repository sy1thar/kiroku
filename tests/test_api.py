import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.seed import seed_if_empty


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        seed_if_empty(db)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


def test_health_and_ssr_pages(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/favicon.ico").status_code == 200
    ru_home = client.get("/ru")
    assert ru_home.status_code == 200
    assert "site-footer" not in ru_home.text
    assert "Найти персонажа<" in ru_home.text
    assert "по внешности" not in ru_home.text or "по внешности и психотипам" in ru_home.text
    assert client.get("/en").status_code == 200
    catalog_res = client.get("/ru/catalog")
    assert catalog_res.status_code == 200
    assert 'name="franchise"' not in catalog_res.text
    assert client.get("/ru/characters").status_code == 200
    assert client.get("/ru/about").status_code == 200
    assert client.get("/en/about").status_code == 200

    media_page = client.get("/ru/media/solo-leveling-manhwa")
    assert media_page.status_code == 200
    assert "/ru/catalog?franchise=solo-leveling" in media_page.text

    char_page = client.get("/ru/characters/sung-jin-woo")
    assert char_page.status_code == 200
    assert "/ru/catalog?franchise=solo-leveling" in char_page.text
    assert client.get("/en/characters/satoru-gojo").status_code == 200


def test_character_filter_query_and_images(client: TestClient):
    res = client.get("/api/characters", params={"q": "Годжо"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["slug"] == "satoru-gojo"

    page = client.get("/ru/characters", params={"q": "2B"})
    assert page.status_code == 200
    assert "yorha-no2-type-b" in page.text
    assert "https://s4.anilist.co/" in page.text


def test_cross_script_search(client: TestClient):
    # Cyrillic query should match Latin/Japanese character Satoru Gojo
    res_ru = client.get("/api/search", params={"q": "Годжо", "locale": "ru"})
    assert res_ru.status_code == 200
    chars_ru = res_ru.json()["characters"]
    assert any(c["slug"] == "satoru-gojo" for c in chars_ru)

    # Japanese query works even when UI locale is Russian
    res_ja = client.get("/api/search", params={"q": "フリーレン", "locale": "ru"})
    assert res_ja.status_code == 200
    assert any(c["slug"] == "frieren" for c in res_ja.json()["characters"])

    # Korean query works even when UI locale is Russian
    res_ko = client.get("/api/search", params={"q": "김독자", "locale": "ru"})
    assert res_ko.status_code == 200
    assert any(c["slug"] == "kim-dokja" for c in res_ko.json()["characters"])

    # Russian query works even when UI locale is English
    res_ru_on_en = client.get("/api/search", params={"q": "Всеведущий читатель", "locale": "en"})
    assert res_ru_on_en.status_code == 200
    assert any(m["slug"] == "omniscient-reader-manhwa" for m in res_ru_on_en.json()["media"])

    # Latin query works across locales
    res_en = client.get("/api/search", params={"q": "Geralt", "locale": "en"})
    assert res_en.status_code == 200
    assert any(c["slug"] == "geralt-of-rivia" for c in res_en.json()["characters"])


def test_appearance_and_mbti_filters(client: TestClient):
    res = client.get("/api/characters", params={"hair_color": "white", "eye_color": "blue", "mbti": "ENTP"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["slug"] == "satoru-gojo"

    # Expanded batch counts: 9 original + 10 new = 19 media, 6 original + 10 new = 16 characters
    assert client.get("/api/media").json()["count"] >= 19
    assert client.get("/api/characters").json()["count"] >= 16


def test_trait_voting_and_wiki_revision_workflow(client: TestClient):
    chars = client.get("/api/characters").json()["items"]
    gojo = next(c for c in chars if c["slug"] == "satoru-gojo")

    # First click votes (+1), second click from same voter toggles vote off (-1)
    vote_1 = client.post("/api/traits/vote", json={"character_id": gojo["id"], "trait_slug": "blindfold"})
    assert vote_1.status_code == 200
    assert vote_1.json()["voted"] is True
    count_after_vote = vote_1.json()["votes_count"]

    vote_2 = client.post("/api/traits/vote", json={"character_id": gojo["id"], "trait_slug": "blindfold"})
    assert vote_2.status_code == 200
    assert vote_2.json()["voted"] is False
    assert vote_2.json()["votes_count"] == count_after_vote - 1

    # Verify 2B & Malenia portraits are real character images
    twob_page = client.get("/ru/characters/yorha-no2-type-b")
    assert twob_page.status_code == 200
    assert "b132494-R05zeZPjDG3l.jpg" in twob_page.text

    malenia_page = client.get("/ru/characters/malenia-blade-of-miquella")
    assert malenia_page.status_code == 200
    assert "b299765-hRQoXwscGpjJ.jpg" in malenia_page.text

    rev_res = client.post(
        "/api/revisions",
        json={
            "entity_type": "character",
            "entity_id": gojo["id"],
            "locale": "ru",
            "field_name": "abilities_summary",
            "proposed_value": "Безграничность, Шесть Глаз и Безграничная Пустота (обновлено)",
            "reason": "Уточнение техник",
        },
    )
    assert rev_res.status_code == 200
    rev_id = rev_res.json()["id"]

    # Guest cannot approve revisions
    assert client.post(f"/api/revisions/{rev_id}/decision", json={"action": "approve"}).status_code == 401

    # Configured admin account Ikazych1 gets admin role and can approve revisions
    client.post(
        "/api/auth/register",
        json={"username": "Ikazych1", "email": "mikolavelikodnij2@gmail.com", "password": "TestAdminPass1!"},
    )
    dec_res = client.post(f"/api/revisions/{rev_id}/decision", json={"action": "approve"})
    assert dec_res.status_code == 200
    assert dec_res.json()["status"] == "approved"

    page = client.get("/ru/characters/satoru-gojo")
    assert "Безграничная Пустота (обновлено)" in page.text


def test_registration_login_settings_and_role_permissions(client: TestClient):
    reg_page = client.get("/ru/register")
    assert reg_page.status_code == 200
    assert "/auth/oauth/" not in reg_page.text
    assert client.get("/auth/oauth/google").status_code == 404
    assert client.get("/ru/login").status_code == 200

    # Regular user registration
    reg = client.post(
        "/api/auth/register",
        json={"username": "nick_kiroku", "email": "nick@example.com", "password": "secret123"},
    )
    assert reg.status_code == 200
    user_id = reg.json()["id"]

    me = client.get("/api/auth/me")
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["username"] == "nick_kiroku"
    assert me.json()["user"]["role"] == "user"

    # Regular user is blocked from admin & import routes
    assert client.get("/ru/import").status_code == 403
    assert client.get("/ru/admin").status_code == 403
    assert client.post("/api/import/batch", json={"category": "anime", "count": 5}).status_code == 403
    assert client.get("/api/admin/users").status_code == 403

    # Account settings update (nickname, avatar, bio, locale, password)
    upd = client.patch(
        "/api/account/profile",
        json={
            "username": "nick_updated",
            "email": "nick@example.com",
            "avatar_url": "https://example.com/avatar.png",
            "bio": "Anime & RPG fan",
            "preferred_locale": "en",
        },
    )
    assert upd.status_code == 200
    assert upd.json()["username"] == "nick_updated"
    assert upd.json()["avatar_url"] == "https://example.com/avatar.png"
    assert client.get("/ru/profile").status_code == 200
    assert client.get("/ru/settings").status_code == 200

    pw = client.post(
        "/api/account/password",
        json={"current_password": "secret123", "new_password": "newsecret456"},
    )
    assert pw.status_code == 200

    # Save a title into the registered user's personal Kiroku list
    media_items = client.get("/api/media").json()["items"]
    frieren = next(m for m in media_items if m["slug"] == "frieren-anime")
    save_res = client.post(
        "/api/lists",
        json={"media_id": frieren["id"], "status": "active", "progress_unit": 12, "score": 10, "is_favorite": True},
    )
    assert save_res.status_code == 200
    assert save_res.json()["profile_key"].startswith("user:")

    lists_page = client.get("/ru/lists")
    assert lists_page.status_code == 200
    assert "card-poster" in lists_page.text
    assert "bx154587-qQTzQnEJJ3oB.jpg" in lists_page.text

    # Admin login & promote user to moderator
    client.post(
        "/api/auth/register",
        json={"username": "Ikazych1", "email": "mikolavelikodnij2@gmail.com", "password": "TestAdminPass1!"},
    )
    assert client.get("/ru/admin").status_code == 200
    assert client.get("/ru/import").status_code == 200

    promote = client.post(
        "/api/admin/users/by-username/role",
        json={"username": "nick_updated", "role": "moderator"},
    )
    assert promote.status_code == 200
    assert promote.json()["role"] == "moderator"

    # Moderator also cannot access /ru/admin or /ru/import
    client.post("/api/auth/login", json={"login": "nick_updated", "password": "newsecret456"})
    assert client.get("/ru/admin").status_code == 403
    assert client.get("/ru/import").status_code == 403
    assert client.post(f"/api/admin/media/{frieren['id']}/import-characters", json={"count": 5}).status_code == 403

    # Log back in as Admin
    client.post("/api/auth/login", json={"login": "Ikazych1", "password": "TestAdminPass1!"})
    assert client.get("/ru/admin").status_code == 200

    # Admin direct edit & delete media
    edit_media = client.patch(
        f"/api/admin/media/{frieren['id']}",
        json={
            "locale": "ru",
            "title": "Фрирен (Админ-правка)",
            "synopsis": "Обновлённое описание",
            "cover_url": "https://example.com/frieren.jpg",
            "rating": 9.8,
            "status": "completed",
        },
    )
    assert edit_media.status_code == 200
    assert client.delete(f"/api/admin/media/{frieren['id']}").status_code == 200


def test_user_tracker_upsert(client: TestClient):
    media_items = client.get("/api/media").json()["items"]
    witcher = next(m for m in media_items if m["slug"] == "the-witcher-3-wild-hunt")

    res = client.post(
        "/api/lists",
        json={
            "media_id": witcher["id"],
            "status": "completed",
            "progress_unit": 120,
            "score": 10,
            "is_favorite": True,
            "notes": "100% completion",
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "completed"
    assert res.json()["progress_unit"] == 120


def test_database_url_normalization_for_cloud_deploy():
    from app.database import normalize_database_url
    from app.services.importers.base import clean_html_markup

    assert normalize_database_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_database_url("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_database_url("sqlite:///./kiroku.db") == "sqlite:///./kiroku.db"
    cleaned = clean_html_markup("__Quirk__ [All Might](https://anilist.co/character/89224)")
    assert cleaned == "Quirk All Might"


def test_community_appearance_custom_traits_and_cosmetic_roles(client: TestClient):
    # Regular user can update appearance & create custom trait tag
    chars = client.get("/api/characters").json()["items"]
    gojo = next(c for c in chars if c["slug"] == "satoru-gojo")

    app_res = client.patch(
        f"/api/characters/{gojo['id']}/appearance",
        json={"hair_color": "white", "eye_color": "blue", "hair_length": "short"},
    )
    assert app_res.status_code == 200
    assert app_res.json()["hair_color"] == "white"

    tr_res = client.post(
        f"/api/traits/custom?character_id={gojo['id']}&name=Стильная%20повязка&category=appearance"
    )
    assert tr_res.status_code == 200

    # Register regular user
    client.post(
        "/api/auth/register",
        json={"username": "cosmetic_user", "email": "cos@example.com", "password": "secret123"},
    )

    # Register/login as Admin Ikazych1
    client.post(
        "/api/auth/register",
        json={"username": "Ikazych1", "email": "mikolavelikodnij2@gmail.com", "password": "TestAdminPass1!"},
    )

    # Create two arbitrary custom roles with different frame/nick colors
    r1 = client.post(
        "/api/admin/custom-roles",
        json={
            "name": "Охотник S-ранга",
            "badge_color": "#f59e0b",
            "nick_color": "#fcd34d",
            "avatar_frame": "glow",
            "frame_color": "#f59e0b",
        },
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/admin/custom-roles",
        json={
            "name": "Хранитель Лора",
            "badge_color": "#ec4899",
            "nick_color": "#f9a8d4",
            "avatar_frame": "rainbow",
            "frame_color": "#ec4899",
        },
    )
    assert r2.status_code == 200

    # Admin assigns BOTH custom roles to themselves AND to cosmetic_user
    for target_nick in ("Ikazych1", "cosmetic_user"):
        for rid in (r1.json()["id"], r2.json()["id"]):
            asg = client.post(
                "/api/admin/custom-roles/assign",
                json={"username": target_nick, "role_id": rid},
            )
            assert asg.status_code == 200

    # Admin can also grant moderator role to themselves without losing admin
    self_mod = client.post(
        "/api/admin/users/by-username/role",
        json={"username": "Ikazych1", "role": "moderator"},
    )
    assert self_mod.status_code == 200
    assert self_mod.json()["role"] == "admin"
    assert self_mod.json()["is_moderator"] is True

    # Admin page uses custom Kiroku swatch picker instead of OS color dialog
    admin_html = client.get("/ru/admin").text
    assert 'type="color"' not in admin_html
    assert "data-picker-target" in admin_html

    # Public & private profile pages render Admin, Moderator, and all custom badges
    pub = client.get("/ru/u/Ikazych1")
    assert pub.status_code == 200
    assert "Админ" in pub.text
    assert "Модератор" in pub.text
    assert "Охотник S-ранга" in pub.text
    assert "Хранитель Лора" in pub.text
    assert "frame-glow" in pub.text

    priv = client.get("/ru/profile")
    assert priv.status_code == 200
    assert "Админ" in priv.text
    assert "Модератор" in priv.text
    assert "Охотник S-ранга" in priv.text

    # Cosmetic roles grant NO technical permissions to cosmetic_user
    client.post("/api/auth/login", json={"login": "cosmetic_user", "password": "secret123"})
    assert client.get("/ru/admin").status_code == 403
    assert client.get("/ru/import").status_code == 403
    assert client.post("/api/import/batch", json={"category": "anime", "count": 10}).status_code == 403
    assert (
        client.post(
            "/api/admin/media/1/import-characters",
            json={"count": 12, "locale": "ru", "start_page": 1},
        ).status_code
        == 403
    )
    assert client.post("/api/admin/translate-missing").status_code == 403


def test_auto_loop_ui_and_character_pagination(client: TestClient, monkeypatch):
    """Admin sees continuous auto-loop controls and paginated character import supports start_page."""
    from app.main import app
    from app.database import get_db
    from app.models import ExternalMapping, Media, MediaTranslation
    from app.schemas import AdminImportCharactersRequest

    # Schema accepts start_page for loop continuation
    req = AdminImportCharactersRequest(count=12, locale="ru", start_page=3)
    assert req.start_page == 3

    # Register & log in as admin Ikazych1
    login = client.post(
        "/api/auth/register",
        json={"username": "Ikazych1", "email": "mikolavelikodnij2@gmail.com", "password": "TestAdminPass1!"},
    )
    assert login.status_code == 200

    # UI contains auto-loop controls, global mini-widget, character loop on /import, and hidden Stop buttons
    imp_html = client.get("/ru/import").text
    assert "loop-start-btn" in imp_html
    assert "cloop-start-btn" in imp_html
    assert "startTitlesAutoLoop" in imp_html
    assert "startCharsAutoLoop" in imp_html
    assert 'id="loop-stop-btn"' in imp_html and "display:none;" in imp_html
    assert 'id="kiroku-loop-mini"' in imp_html
    assert "/static/js/kiroku.js?v=" in imp_html

    adm_html = client.get("/ru/admin").text
    assert "tr-loop-start-btn" in adm_html
    assert "loopTranslateMissing" in adm_html
    assert 'id="kiroku-loop-mini"' in adm_html

    db_gen = app.dependency_overrides[get_db]()
    db_session = next(db_gen)
    try:
        m = Media(slug="loop-test-anime", media_type="anime", canonical_title="Loop Test Anime", status="ongoing", release_year=2025)
        db_session.add(m)
        db_session.flush()
        db_session.add(MediaTranslation(media_id=m.id, locale="ru", title="Луп Тест", synopsis="Тест"))
        db_session.add(
            ExternalMapping(
                entity_type="media",
                entity_id=m.id,
                provider="anilist",
                external_id="999999",
                external_url="https://anilist.co/anime/999999",
            )
        )
        db_session.commit()
        media_id = m.id
        media_slug = m.slug
    finally:
        db_gen.close()

    det_html = client.get(f"/ru/media/{media_slug}").text
    assert "char-loop-start-btn" in det_html
    assert "adminLoopImportChars" in det_html

    # Mock httpx.AsyncClient + Shikimori translation for 2 paginated waves of characters
    import httpx

    pages_seen = []

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=None)

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            vars_ = (json or {}).get("variables") or {}
            page = vars_.get("page", 1)
            pages_seen.append(page)
            if page == 1:
                edges = [
                    {"role": "MAIN", "node": {"id": 800001 + i, "name": {"full": f"Hero {i}"}, "description": "", "image": {}, "favourites": 10}, "voiceActors": []}
                    for i in range(3)
                ]
                has_next = True
            else:
                edges = [
                    {"role": "SUPPORTING", "node": {"id": 800010, "name": {"full": "Final Hero"}, "description": "", "image": {}, "favourites": 5}, "voiceActors": []}
                ]
                has_next = False
            return FakeResp({"data": {"Media": {"characters": {"pageInfo": {"hasNextPage": has_next}, "edges": edges}}}})

    async def noop_shiki(name):
        return ""

    async def noop_tr(text, target="ru"):
        return text, False

    monkeypatch.setattr("app.services.importers.anilist.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.services.importers.anilist.fetch_shikimori_character_ru", noop_shiki)
    monkeypatch.setattr("app.services.importers.anilist.translate_text", noop_tr)

    wave1 = client.post(
        f"/api/admin/media/{media_id}/import-characters",
        json={"count": 2, "locale": "ru", "start_page": 1},
    )
    assert wave1.status_code == 200
    d1 = wave1.json()
    assert d1["added_count"] == 2
    assert d1["next_page"] == 1
    assert d1["has_more"] is True

    wave2 = client.post(
        f"/api/admin/media/{media_id}/import-characters",
        json={"count": 5, "locale": "ru", "start_page": d1["next_page"]},
    )
    assert wave2.status_code == 200
    d2 = wave2.json()
    assert d2["added_count"] == 2
    assert d2["has_more"] is False
    assert d2["media_exhausted"] is True
    assert pages_seen == [1, 1, 2]

    # Once all characters for a title are loaded, character import buttons disappear from DOM on media detail page
    det_after = client.get(f"/ru/media/{media_slug}").text
    assert 'id="char-import-controls"' not in det_after
    assert 'id="char-loop-start-btn"' not in det_after

    # TVMaze 404 exhaustion returns cleanly with has_next_page=False instead of crashing loop
    class Fake404Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return FakeResp({}, status_code=404)

    monkeypatch.setattr("app.services.importers.batch.httpx.AsyncClient", Fake404Client)
    end_res = client.post("/api/import/batch", json={"category": "series", "count": 10})
    assert end_res.status_code == 200
    assert end_res.json()["has_next_page"] is False
    assert end_res.json()["imported_count"] == 0

    # Background loop status & stop endpoints work and adhere to saved locale setting
    client.get("/set-locale/en?next=/en/import", follow_redirects=False)
    loop_st = client.post(
        "/api/admin/loop/start",
        json={"kind": "characters_all", "categories": ["anime"], "batch_size": 10, "max_items": 10, "delay_ms": 0},
    )
    assert loop_st.status_code == 200
    assert loop_st.json()["locale"] == "en"
    stop_res = client.post("/api/admin/loop/stop")
    assert stop_res.status_code == 200
    status_res = client.get("/api/admin/loop/status")
    assert status_res.status_code == 200


def test_catalog_mixed_formats_recent_sort_and_pagination(client: TestClient):
    """Catalog 'All formats' interleaves media types, supports sort=recent, and paginates beyond 48 items."""
    from app.database import get_db
    from app.main import app
    from app.models import Media, MediaTranslation

    db_gen = app.dependency_overrides[get_db]()
    db = next(db_gen)
    try:
        # Insert 55 ultra-high popularity anime + 1 low-popularity newly added game + 1 series
        for idx in range(55):
            m = Media(
                slug=f"high-pop-anime-{idx}",
                media_type="anime",
                canonical_title=f"High Pop Anime {idx}",
                popularity=900_000 - idx,
                rating=8.5,
            )
            db.add(m)
            db.flush()
            db.add(MediaTranslation(media_id=m.id, locale="ru", title=f"Топ Аниме {idx}", synopsis=""))

        recent_game = Media(
            slug="freshly-looped-game",
            media_type="game",
            canonical_title="Freshly Looped Game",
            popularity=50,
            rating=9.1,
        )
        db.add(recent_game)
        db.flush()
        db.add(MediaTranslation(media_id=recent_game.id, locale="ru", title="Свежая Игра", synopsis=""))
        db.commit()
    finally:
        db_gen.close()

    # Default 'All formats' on page 1 must mix formats instead of showing 48 anime only
    cat_p1 = client.get("/ru/catalog")
    assert cat_p1.status_code == 200
    assert "Freshly Looped Game" in cat_p1.text or "Свежая Игра" in cat_p1.text
    assert "/ru/catalog?q=&amp;type=&amp;franchise=&amp;sort=popularity&amp;page=2" in cat_p1.text or "page=2" in cat_p1.text

    # sort=recent shows newly imported items right at the top
    cat_recent = client.get("/ru/catalog?sort=recent")
    assert cat_recent.status_code == 200
    assert "Свежая Игра" in cat_recent.text

    # page=2 loads next slice
    cat_p2 = client.get("/ru/catalog?page=2")
    assert cat_p2.status_code == 200
    assert "Топ Аниме" in cat_p2.text
