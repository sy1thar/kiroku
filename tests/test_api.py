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

    dec_res = client.post(f"/api/revisions/{rev_id}/decision", json={"action": "approve"})
    assert dec_res.status_code == 200
    assert dec_res.json()["status"] == "approved"

    page = client.get("/ru/characters/satoru-gojo")
    assert "Безграничная Пустота (обновлено)" in page.text


def test_registration_login_and_personal_lists_with_covers(client: TestClient):
    reg_page = client.get("/ru/register")
    assert reg_page.status_code == 200
    for provider_name in ("Google", "Apple", "Discord", "Telegram", "GitHub", "VK"):
        assert provider_name in reg_page.text

    assert client.get("/ru/login").status_code == 200
    oauth_unconfigured = client.get("/auth/oauth/google")
    assert oauth_unconfigured.status_code == 200
    assert "GOOGLE_CLIENT_ID" in oauth_unconfigured.text

    reg = client.post(
        "/api/auth/register",
        json={"username": "nick_kiroku", "email": "nick@example.com", "password": "secret123"},
    )
    assert reg.status_code == 200
    assert reg.json()["username"] == "nick_kiroku"

    me = client.get("/api/auth/me")
    assert me.json()["authenticated"] is True
    assert me.json()["user"]["username"] == "nick_kiroku"

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

    assert normalize_database_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_database_url("postgresql://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert normalize_database_url("sqlite:///./kiroku.db") == "sqlite:///./kiroku.db"
