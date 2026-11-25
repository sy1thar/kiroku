import asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Character, ExternalMapping, Media
from app.services.importers.base import upsert_character_from_external, upsert_media_from_external


def test_external_mapping_prevents_duplicates():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    async def run():
        with SessionLocal() as db:
            m1 = await upsert_media_from_external(
                db,
                provider="anilist",
                external_id="154587",
                external_url="https://anilist.co/anime/154587",
                media_type="anime",
                canonical_title="Frieren: Beyond Journey's End",
                release_year=2023,
                rating=9.1,
                synopsis_en="<p>An elf mage travels northward after the Demon King's defeat.</p>",
            )
            m2 = await upsert_media_from_external(
                db,
                provider="anilist",
                external_id="154587",
                external_url="https://anilist.co/anime/154587",
                media_type="anime",
                canonical_title="Frieren: Beyond Journey's End",
                release_year=2023,
                rating=9.2,
                synopsis_en="Updated synopsis",
            )
            assert m1.id == m2.id

            c1 = await upsert_character_from_external(
                db,
                provider="anilist",
                external_id="176754",
                external_url="https://anilist.co/character/176754",
                canonical_name="Frieren",
                hair_color="white",
                eye_color="green",
                media_id=m1.id,
                role="main",
                voice_actor="Atsumi Tanezaki",
            )
            c2 = await upsert_character_from_external(
                db,
                provider="anilist",
                external_id="176754",
                external_url="https://anilist.co/character/176754",
                canonical_name="Frieren",
                media_id=m1.id,
                role="main",
            )
            db.commit()

            assert c1.id == c2.id
            assert len(db.scalars(select(Media)).all()) == 1
            assert len(db.scalars(select(Character)).all()) == 1
            assert len(db.scalars(select(ExternalMapping)).all()) == 2

    asyncio.run(run())
