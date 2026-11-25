from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Character,
    CharacterTrait,
    CharacterTranslation,
    ExternalMapping,
    Franchise,
    Media,
    MediaCharacter,
    MediaTranslation,
    Trait,
    User,
    UserListEntry,
)
from app.services.auth import hash_password


SEED_MEDIA_COVERS: dict[str, str] = {
    "solo-leveling-manhwa": "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx105398-b673Vt5ZSuz3.jpg",
    "solo-leveling-anime": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx151807-it355ZgzquUd.png",
    "the-witcher-3-wild-hunt": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/292030/library_600x900_2x.jpg",
    "fate-stay-night-vn": "https://t.vndb.org/cv/71/89071.jpg",
    "fate-zero-anime": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx10087-M4Hd9qrHGrXk.png",
    "nier-automata-game": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/524220/library_600x900_2x.jpg",
    "nier-automata-anime": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx145665-Qs53Mta5ngqs.jpg",
    "jujutsu-kaisen-anime": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx113415-LHBAeoZDIsnF.jpg",
    "steins-gate-vn": "https://t.vndb.org/cv/19/77819.jpg",
}

SEED_CHARACTER_IMAGES: dict[str, str] = {
    "sung-jin-woo": "https://s4.anilist.co/file/anilistcdn/character/large/b129928-BCEjVaP0AQSw.png",
    "geralt-of-rivia": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/292030/header.jpg",
    "satoru-gojo": "https://s4.anilist.co/file/anilistcdn/character/large/b127691-9zqh1xpIubn7.png",
    "yorha-no2-type-b": "https://s4.anilist.co/file/anilistcdn/character/large/b132494-R05zeZPjDG3l.jpg",
    "saber-artoria-pendragon": "https://s4.anilist.co/file/anilistcdn/character/large/b497-Yg5pNmC8kxzs.png",
    "rintaro-okabe": "https://s4.anilist.co/file/anilistcdn/character/large/b35252-DY9TW6pusqeh.png",
}


def backfill_seed_images(db: Session) -> None:
    """Populate or repair cover/portrait URLs on existing seeded records."""
    changed = False
    for slug, url in SEED_MEDIA_COVERS.items():
        media = db.scalar(select(Media).where(Media.slug == slug))
        if media and not media.cover_url:
            media.cover_url = url
            changed = True
    for slug, url in SEED_CHARACTER_IMAGES.items():
        ch = db.scalar(select(Character).where(Character.slug == slug))
        if ch and (not ch.image_url or "123616-9VUYw9dgcjxL" in ch.image_url):
            ch.image_url = url
            changed = True
    malenia = db.scalar(select(Character).where(Character.slug == "malenia-blade-of-miquella"))
    malenia_real_url = "https://s4.anilist.co/file/anilistcdn/character/large/b299765-hRQoXwscGpjJ.jpg"
    if malenia and malenia.image_url != malenia_real_url:
        malenia.image_url = malenia_real_url
        changed = True
    if changed:
        db.commit()


def ensure_admin_account(db: Session) -> None:
    """Promote configured admin accounts to 'admin' and optionally create from ADMIN_PASSWORD env."""
    settings = get_settings()
    changed = False
    for user in db.scalars(
        select(User).where(
            or_(
                func.lower(User.username).in_(settings.admin_username_set),
                func.lower(User.email).in_(settings.admin_email_set),
            )
        )
    ).all():
        if user.role != "admin":
            user.role = "admin"
            changed = True

    if settings.admin_password and settings.admin_username_set and settings.admin_email_set:
        uname = next(iter(settings.admin_usernames.split(","))).strip() or "Ikazych1"
        uemail = next(iter(settings.admin_emails.split(","))).strip().lower()
        existing = db.scalar(
            select(User).where(
                or_(func.lower(User.username) == uname.lower(), func.lower(User.email) == uemail)
            )
        )
        if not existing:
            db.add(
                User(
                    username=uname,
                    email=uemail,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                )
            )
            changed = True

    if changed:
        db.commit()


def seed_if_empty(db: Session) -> None:
    """Populate initial cross-media universe (Anime, Manhwa, Games, Visual Novels) if DB is empty."""
    ensure_admin_account(db)
    existing_count = db.scalar(select(func.count(Media.id))) or 0
    if existing_count > 0:
        backfill_seed_images(db)
        seed_extra_batch(db)
        return

    # 1. Traits (ACDB appearance + PDB personality + combat/abilities)
    traits_data = [
        ("white-hair", "appearance", "White / Silver Hair", "Белые / серебряные волосы"),
        ("black-hair", "appearance", "Jet Black Hair", "Чёрные волосы"),
        ("blonde-hair", "appearance", "Golden Blonde", "Золотистый блонд"),
        ("blue-eyes", "appearance", "Glowing Blue Eyes", "Голубые глаза"),
        ("scar", "appearance", "Battle Scar", "Боевой шрам"),
        ("blindfold", "appearance", "Blindfold / Visor", "Повязка на глазах"),
        ("necromancy", "ability", "Shadow Necromancy", "Некромантия теней"),
        ("swordsmanship", "ability", "Master Swordsmanship", "Мастер меча"),
        ("time-travel", "ability", "Time Leap / Causality", "Путешествия во времени"),
        ("alchemy-signs", "ability", "Signs & Alchemy", "Ведьмачьи знаки и алхимия"),
        ("antihero", "personality", "Stoic Antihero", "Стоический антигерой"),
        ("mentor", "personality", "Overpowered Mentor", "Имбовый наставник"),
        ("mad-scientist", "personality", "Eccentric Genius", "Эксцентричный гений"),
        ("knight-honor", "personality", "Chivalrous Idealist", "Рыцарский идеализм"),
    ]
    traits_by_slug: dict[str, Trait] = {}
    for slug, cat, n_en, n_ru in traits_data:
        tr = Trait(slug=slug, category=cat, name_en=n_en, name_ru=n_ru)
        db.add(tr)
        traits_by_slug[slug] = tr
    db.flush()

    # 2. Cross-Media Franchises
    franchises = {
        "solo-leveling": Franchise(
            slug="solo-leveling",
            canonical_name="Solo Leveling",
            description_ru="Корейская вселенная о пробуждённых охотниках, вратах подземелий и Теневом Монархе — от ранобэ и легендарной манхвы до аниме и экшн-RPG.",
            description_en="Korean dark fantasy universe of hunters, dungeon gates, and the Shadow Monarch — spanning web novel, iconic manhwa, anime, and action RPG.",
        ),
        "the-witcher": Franchise(
            slug="the-witcher",
            canonical_name="The Witcher",
            description_ru="Тёмное славянское фэнтези Анджея Сапковского, прославленное трилогией игр CD Projekt RED, книгами и сериалом.",
            description_en="Dark fantasy continent created by Andrzej Sapkowski, spanning bestselling novels, CD Projekt RED RPGs, and live-action/animated series.",
        ),
        "fate": Franchise(
            slug="fate",
            canonical_name="Fate",
            description_ru="Масштабная мультивселенная Type-Moon о Войнах Святого Грааля, объединяющая визуальные новеллы, аниме Ufotable и игры.",
            description_en="Type-Moon's sprawling urban fantasy multiverse of Holy Grail Wars spanning visual novels, Ufotable anime adaptations, and RPGs.",
        ),
        "nier": Franchise(
            slug="nier",
            canonical_name="NieR",
            description_ru="Философский постапокалипсис Йоко Таро об андроидах и машинах: от культовой игры до аниме-адаптации.",
            description_en="Yoko Taro's philosophical post-apocalyptic saga of YoRHa androids and machines across video games and anime.",
        ),
        "jujutsu-kaisen": Franchise(
            slug="jujutsu-kaisen",
            canonical_name="Jujutsu Kaisen",
            description_ru="Современное тёмное городское фэнтези о проклятиях, магах и Домене Бесконечности.",
            description_en="Modern dark urban fantasy about curses, sorcerers, and Domain Expansions.",
        ),
        "steins-gate": Franchise(
            slug="steins-gate",
            canonical_name="Steins;Gate",
            description_ru="Научно-фантастический триллер о путешествиях во времени, линиях мира и Лаборатории Гаджетов Будущего.",
            description_en="Sci-fi thriller about D-Mails, worldlines, and the Future Gadget Laboratory across visual novel and anime.",
        ),
    }
    for fr in franchises.values():
        db.add(fr)
    db.flush()

    # 3. Media across Anime, Manhwa, Game, VN, Novel, Manga
    media_specs = [
        dict(
            slug="solo-leveling-manhwa",
            franchise="solo-leveling",
            media_type="manhwa",
            canonical_title="Solo Leveling",
            native_title="나 혼자만 레벨업",
            release_year=2018,
            status="completed",
            rating=8.9,
            popularity=98000,
            total_units=200,
            unit_label="ch",
            genres=["Action", "Fantasy", "Dark Fantasy"],
            platforms=["KakaoPage", "Webtoon"],
            title_ru="Поднятие уровня в одиночку (Манхва)",
            synopsis_en="Ten years ago, 'the Gate' appeared connecting the real world with monsters. Weakest E-rank hunter Sung Jin-Woo gains a secret System that lets him level up infinitely.",
            synopsis_ru="Десять лет назад появились Врата в мир монстров. Слабейший охотник E-ранга Сон Джин-Ву получает тайную Систему, позволяющую ему бесконечно повышать уровень в одиночку.",
            provider="anilist",
            ext_id="105398",
        ),
        dict(
            slug="solo-leveling-anime",
            franchise="solo-leveling",
            media_type="anime",
            canonical_title="Solo Leveling (Anime)",
            native_title="俺だけレベルアップな件",
            release_year=2024,
            status="ongoing",
            rating=8.6,
            popularity=94000,
            total_units=25,
            unit_label="ep",
            genres=["Action", "Adventure", "Fantasy"],
            platforms=["Crunchyroll"],
            title_ru="Поднятие уровня в одиночку (Аниме)",
            synopsis_en="A-1 Pictures anime adaptation following Sung Jin-Woo's transformation from the weakest hunter into the Shadow Monarch.",
            synopsis_ru="Аниме-адаптация студии A-1 Pictures о пути Сон Джин-Ву от слабейшего охотника человечества до Теневого Монарха.",
            provider="anilist",
            ext_id="151807",
        ),
        dict(
            slug="the-witcher-3-wild-hunt",
            franchise="the-witcher",
            media_type="game",
            canonical_title="The Witcher 3: Wild Hunt",
            native_title="Wiedźmin 3: Dziki Gon",
            release_year=2015,
            status="completed",
            rating=9.5,
            popularity=115000,
            total_units=120,
            unit_label="hr",
            genres=["RPG", "Open World", "Dark Fantasy"],
            platforms=["PC", "PS5", "Xbox", "Switch"],
            title_ru="Ведьмак 3: Дикая Охота",
            synopsis_en="Monster slayer Geralt of Rivia searches for his adopted daughter Ciri across war-torn Northern Kingdoms while evading the spectral Wild Hunt.",
            synopsis_ru="Охотник на чудовищ Геральт из Ривии разыскивает свою приёмную дочь Цири в охваченных войной Северных Королевствах, спасаясь от Дикой Охоты.",
            provider="igdb",
            ext_id="1942",
        ),
        dict(
            slug="fate-stay-night-vn",
            franchise="fate",
            media_type="vn",
            canonical_title="Fate/stay night",
            native_title="フェイト/ステイナイト",
            release_year=2004,
            status="completed",
            rating=9.0,
            popularity=78000,
            total_units=65,
            unit_label="hr",
            genres=["Urban Fantasy", "Action", "Drama"],
            platforms=["PC", "Switch"],
            title_ru="Судьба / Ночь схватки (Визуальная новелла)",
            synopsis_en="High school magus Shirou Emiya accidentally summons the heroic spirit Saber and enters the deadly Fifth Holy Grail War in Fuyuki City.",
            synopsis_ru="Школьник-маг Сиро Эмия случайно призывает героическую душу Сэйбер и вступает в смертельную Пятую Войну Святого Грааля в Фуюки.",
            provider="vndb",
            ext_id="v11",
        ),
        dict(
            slug="fate-zero-anime",
            franchise="fate",
            media_type="anime",
            canonical_title="Fate/Zero",
            native_title="フェイト/ゼロ",
            release_year=2011,
            status="completed",
            rating=8.8,
            popularity=89000,
            total_units=25,
            unit_label="ep",
            genres=["Action", "Dark Fantasy", "Psychological"],
            platforms=["Crunchyroll"],
            title_ru="Судьба / Начало",
            synopsis_en="Ten years before Fate/stay night, seven mages and legendary Heroic Spirits clash in a ruthless battle royale for the wish-granting Holy Grail.",
            synopsis_ru="За десять лет до событий Fate/stay night семь магов и призванные ими легендарные Героические Духи сходятся в безжалостной битве за Святой Грааль.",
            provider="anilist",
            ext_id="10087",
        ),
        dict(
            slug="nier-automata-game",
            franchise="nier",
            media_type="game",
            canonical_title="NieR:Automata",
            native_title="ニーア オートマタ",
            release_year=2017,
            status="completed",
            rating=9.2,
            popularity=91000,
            total_units=45,
            unit_label="hr",
            genres=["Action RPG", "Sci-Fi", "Philosophical"],
            platforms=["PC", "PS4", "Switch", "Xbox"],
            title_ru="NieR:Automata (Игра)",
            synopsis_en="Androids 2B and 9S fight a proxy war against machine lifeforms to reclaim Earth for humanity, uncovering devastating truths about their existence.",
            synopsis_ru="Андроиды 2B и 9S ведут войну против машин за возвращение Земли человечеству, постепенно раскрывая трагическую правду о своём существовании.",
            provider="igdb",
            ext_id="11208",
        ),
        dict(
            slug="nier-automata-anime",
            franchise="nier",
            media_type="anime",
            canonical_title="NieR:Automata Ver1.1a",
            native_title="ニーア オートマタ Ver1.1a",
            release_year=2023,
            status="completed",
            rating=8.2,
            popularity=64000,
            total_units=24,
            unit_label="ep",
            genres=["Action", "Sci-Fi", "Drama"],
            platforms=["Crunchyroll"],
            title_ru="NieR:Automata Версия 1.1a (Аниме)",
            synopsis_en="Animated adaptation reimagining the tragic YoRHa campaign of 2B and 9S in the year 11945 AD.",
            synopsis_ru="Аниме-адаптация культовой игры с новыми сюжетными развилками о миссии андроидов YoRHa в 11945 году.",
            provider="anilist",
            ext_id="145665",
        ),
        dict(
            slug="jujutsu-kaisen-anime",
            franchise="jujutsu-kaisen",
            media_type="anime",
            canonical_title="Jujutsu Kaisen",
            native_title="呪術廻戦",
            release_year=2020,
            status="ongoing",
            rating=8.7,
            popularity=110000,
            total_units=47,
            unit_label="ep",
            genres=["Action", "Supernatural", "Dark Fantasy"],
            platforms=["Crunchyroll"],
            title_ru="Магическая битва",
            synopsis_en="Yuji Itadori swallows a cursed talisman—Sukuna's finger—and joins Tokyo Jujutsu High under the guidance of the strongest sorcerer, Satoru Gojo.",
            synopsis_ru="Юдзи Итадори проглатывает проклятый палец Двуликого Сукуны и поступает в Токийский магический техникум под наставничество сильнейшего мага Сатору Годжо.",
            provider="anilist",
            ext_id="113415",
        ),
        dict(
            slug="steins-gate-vn",
            franchise="steins-gate",
            media_type="vn",
            canonical_title="Steins;Gate",
            native_title="シュタインズ・ゲート",
            release_year=2009,
            status="completed",
            rating=9.3,
            popularity=74000,
            total_units=35,
            unit_label="hr",
            genres=["Sci-Fi", "Thriller", "Psychological"],
            platforms=["PC", "PS4", "Switch"],
            title_ru="Врата Штейна (Визуальная новелла)",
            synopsis_en="Self-proclaimed mad scientist Rintaro Okabe discovers a way to send text messages into the past, triggering catastrophic shifts across worldlines.",
            synopsis_ru="Самопровозглашённый безумный учёный Ринтаро Окабэ случайно изобретает способ отправлять сообщения в прошлое, запуская цепочку трагедий между линиями мира.",
            provider="vndb",
            ext_id="v2002",
        ),
    ]

    media_map: dict[str, Media] = {}
    for spec in media_specs:
        m = Media(
            slug=spec["slug"],
            franchise_id=franchises[spec["franchise"]].id,
            media_type=spec["media_type"],
            canonical_title=spec["canonical_title"],
            native_title=spec["native_title"],
            release_year=spec["release_year"],
            status=spec["status"],
            rating=spec["rating"],
            popularity=spec["popularity"],
            cover_url=SEED_MEDIA_COVERS.get(spec["slug"], ""),
            total_units=spec["total_units"],
            unit_label=spec["unit_label"],
            genres=spec["genres"],
            platforms=spec["platforms"],
        )
        db.add(m)
        db.flush()
        db.add(
            MediaTranslation(
                media_id=m.id,
                locale="en",
                title=spec["canonical_title"],
                synopsis=spec["synopsis_en"],
                source="community_verified",
            )
        )
        db.add(
            MediaTranslation(
                media_id=m.id,
                locale="ru",
                title=spec["title_ru"],
                synopsis=spec["synopsis_ru"],
                source="community_verified",
            )
        )
        db.add(
            ExternalMapping(
                entity_type="media",
                entity_id=m.id,
                provider=spec["provider"],
                external_id=spec["ext_id"],
            )
        )
        media_map[spec["slug"]] = m

    # 4. Cross-Media Characters with ACDB Visuals + PDB MBTI + Anti-Spoiler Lore
    char_specs = [
        dict(
            slug="sung-jin-woo",
            franchise="solo-leveling",
            canonical_name="Sung Jin-Woo",
            native_name="성진우",
            name_ru="Сон Джин-Ву",
            gender="male",
            hair_color="black",
            eye_color="blue",
            hair_length="short",
            mbti="INTJ",
            enneagram="5w6",
            archetype="Shadow Monarch",
            birthday="March 8",
            popularity=99500,
            bio_en="Once mocked as humanity's weakest E-rank hunter, Sung Jin-Woo survives a deadly double dungeon and becomes the sole Player of the System.",
            bio_ru="Некогда презираемый как слабейший охотник E-ранга, Сон Джин-Ву выживает в смертельном двойном подземелье и становится единственным Игроком Системы.",
            appearance_ru="Высокий стройный юноша с чёрными волосами, холодным взглядом и светящимися сине-фиолетовыми глазами при активации маны.",
            abilities_ru="Армия теней (призыв павших врагов вроде Игриса и Беру), телепортация через тень, кинжалы Касаки и Гнев Камиша.",
            spoiler_ru="В финальных арках выясняется, что Система была создана Архитектором для подготовки тела Джин-Ву к полному наследованию силы Ашборна — истинного Теневого Монарха, чтобы остановить войну Владык.",
            traits=["black-hair", "blue-eyes", "necromancy", "antihero"],
            links=[
                ("solo-leveling-manhwa", "main", "Webtoon Protagonist", None),
                ("solo-leveling-anime", "main", "Taito Ban", None),
            ],
        ),
        dict(
            slug="geralt-of-rivia",
            franchise="the-witcher",
            canonical_name="Geralt of Rivia",
            native_name="Geralt z Rivii",
            name_ru="Геральт из Ривии",
            gender="male",
            hair_color="white",
            eye_color="amber",
            hair_length="medium",
            mbti="ISTP",
            enneagram="9w8",
            archetype="Reluctant Monster Slayer",
            birthday="Unknown",
            popularity=96000,
            bio_en="Known as the White Wolf, Geralt is a mutated witcher trained at Kaer Morhen to hunt monsters for coin while navigating brutal human politics.",
            bio_ru="Известный как Белый Волк, Геральт — мутант-ведьмак из Школы Волка, который охотится на чудовищ за плату и пытается сохранить нейтралитет среди интриг королей.",
            appearance_ru="Атлетичный мужчина с пепельно-белыми волосами, вертикальными кошачьими янтарными зрачками и глубоким шрамом через левый глаз.",
            abilities_ru="Два меча (стальной и серебряный), ведьмачьи Знаки (Игни, Квен, Аард, Аксий, Ирден), эликсиры и обострённые чувства.",
            spoiler_ru="В концовке «Ведьмак 3» судьба Геральта и Цири напрямую зависит от моральных выборов игрока как наставника: Цири может стать ведьмачкой, императрицей Нильфгаарда или исчезнуть в Белом Хладе.",
            traits=["white-hair", "scar", "swordsmanship", "alchemy-signs", "antihero"],
            links=[
                ("the-witcher-3-wild-hunt", "main", "Doug Cockle / Vsevolod Kuznetsov", None),
            ],
        ),
        dict(
            slug="satoru-gojo",
            franchise="jujutsu-kaisen",
            canonical_name="Satoru Gojo",
            native_name="五条悟",
            name_ru="Сатору Годжо",
            gender="male",
            hair_color="white",
            eye_color="blue",
            hair_length="short",
            mbti="ENTP",
            enneagram="7w8",
            archetype="The Strongest Sorcerer",
            birthday="December 7",
            popularity=105000,
            bio_en="A teacher at Tokyo Jujutsu High and the undisputed strongest jujutsu sorcerer alive, wielding both the Limitless technique and the Six Eyes.",
            bio_ru="Преподаватель Токийского магического техникума и сильнейший маг современности. За беззаботной улыбкой скрывает холодный расчёт и стремление реформировать мир магов.",
            appearance_ru="Высокий светлокожий мужчина со взъерошенными белоснежными волосами, обычно скрывающий свои ярко-голубые глаза («Шесть Глаз») за чёрной повязкой.",
            abilities_ru="Безграничность (Бесконечность, Синий, Красный, Пурпурный), Шесть Глаз и Расширение Территории «Безграничная Пустота».",
            spoiler_ru="Во время инцидента в Сибуе Кэндзяку использует эмоциональный шок от встречи со старым телом Гэто и запечатывает Годжо в Тюремном Царстве.",
            traits=["white-hair", "blue-eyes", "blindfold", "mentor"],
            links=[
                ("jujutsu-kaisen-anime", "main", "Yuichi Nakamura", None),
            ],
        ),
        dict(
            slug="yorha-no2-type-b",
            franchise="nier",
            canonical_name="2B (YoRHa No.2 Type B)",
            native_name="ヨルハ二号B型",
            name_ru="2B (YoRHa №2 Тип B)",
            gender="female",
            hair_color="white",
            eye_color="blue",
            hair_length="short",
            mbti="ISTJ",
            enneagram="1w9",
            archetype="Tragic Executioner",
            birthday="Unknown",
            popularity=92000,
            bio_en="An all-purpose battle android deployed to Earth to eliminate machine lifeforms. She suppresses emotion behind a strict military facade.",
            bio_ru="Боевой андроид элитного отряда YoRHa, отправленный на Землю для зачистки машин. Скрывает глубокую привязанность к 9S за строгой дисциплиной.",
            appearance_ru="Короткое серебристо-белое каре, чёрное готическое платье, боевая повязка-визор на глазах и родинка у губ.",
            abilities_ru="Виртуозное владение катанами и тяжёлыми клинками, тактическая поддержка Пода 042, уклонение со смещением фазы.",
            spoiler_ru="Настоящее обозначение 2B — 2E (Executioner). Её скрытая директива — казнить напарника 9S каждый раз, когда тот узнаёт правду об исчезновении человечества.",
            traits=["white-hair", "blindfold", "swordsmanship", "antihero"],
            links=[
                ("nier-automata-game", "main", "Yui Ishikawa / Kira Buckland", None),
                ("nier-automata-anime", "main", "Yui Ishikawa", None),
            ],
        ),
        dict(
            slug="saber-artoria-pendragon",
            franchise="fate",
            canonical_name="Saber (Artoria Pendragon)",
            native_name="アルトリア・ペンドラゴン",
            name_ru="Сэйбер (Артория Пендрагон)",
            gender="female",
            hair_color="blonde",
            eye_color="green",
            hair_length="medium",
            mbti="ISFJ",
            enneagram="1w2",
            archetype="King of Knights",
            birthday="Unknown",
            popularity=90000,
            bio_en="The legendary King of Britain summoned as a Saber-class Servant. Guided by unwavering honor, she seeks the Holy Grail to save her fallen kingdom.",
            bio_ru="Легендарный Король Артур, призванный в качестве Слуги класса Сэйбер. Непреклонна в вопросах рыцарской чести и стремится переписать гибель Британии.",
            appearance_ru="Стройная девушка с золотыми волосами, убранными в аккуратный пучок, изумрудными глазами и сине-серебряными доспехами.",
            abilities_ru="Священный меч Экскалибур, невидимые ножны Авалон (регенерация), интуиция боя и драконье сердце.",
            spoiler_ru="Её истинная личность — король Артур из легенд Британии, заключившая контракт с Миром на холме Камланн в попытке найти более достойного правителя вместо себя.",
            traits=["blonde-hair", "swordsmanship", "knight-honor"],
            links=[
                ("fate-stay-night-vn", "main", "Ayako Kawasumi", None),
                ("fate-zero-anime", "main", "Ayako Kawasumi", None),
            ],
        ),
        dict(
            slug="rintaro-okabe",
            franchise="steins-gate",
            canonical_name="Rintaro Okabe",
            native_name="岡部倫太郎",
            name_ru="Ринтаро Окабэ",
            gender="male",
            hair_color="black",
            eye_color="brown",
            hair_length="short",
            mbti="ENTP",
            enneagram="6w7",
            archetype="Hououin Kyouma",
            birthday="December 14",
            popularity=88000,
            bio_en="Founder of the Future Gadget Laboratory who adopts the theatrical persona 'Hououin Kyouma' to mask his deep care for his friends.",
            bio_ru="Основатель Лаборатории Гаджетов Будущего. Прикрывается эксцентричной маской безумного учёного «Хооина Кёмы», чтобы поддерживать друзей.",
            appearance_ru="Высокий худощавый студент с растрёпанными чёрными волосами и неизменным белым лабораторным халатом.",
            abilities_ru="Reading Steiner — уникальная способность сохранять воспоминания при смене мировой линии после отправки D-Mail.",
            spoiler_ru="Чтобы спасти одновременно Маюри Сиину и Курису Макисэ, Окабэ вынужден обмануть самого себя из прошлого, инсценировав смерть Курису и открыв Врата Штейна.",
            traits=["black-hair", "time-travel", "mad-scientist"],
            links=[
                ("steins-gate-vn", "main", "Mamoru Miyano", None),
            ],
        ),
    ]

    for c_spec in char_specs:
        ch = Character(
            slug=c_spec["slug"],
            franchise_id=franchises[c_spec["franchise"]].id,
            canonical_name=c_spec["canonical_name"],
            native_name=c_spec["native_name"],
            gender=c_spec["gender"],
            hair_color=c_spec["hair_color"],
            eye_color=c_spec["eye_color"],
            hair_length=c_spec["hair_length"],
            mbti=c_spec["mbti"],
            enneagram=c_spec["enneagram"],
            archetype=c_spec["archetype"],
            birthday=c_spec["birthday"],
            image_url=SEED_CHARACTER_IMAGES.get(c_spec["slug"], ""),
            popularity=c_spec["popularity"],
        )
        db.add(ch)
        db.flush()

        db.add(
            CharacterTranslation(
                character_id=ch.id,
                locale="en",
                name=c_spec["canonical_name"],
                bio=c_spec["bio_en"],
                appearance_notes=f"{c_spec['hair_color'].title()} hair, {c_spec['eye_color']} eyes.",
                abilities_summary=c_spec["archetype"],
                spoiler_bio="Major late-story revelation protected by Kiroku Anti-Spoiler Guard.",
                source="community_verified",
            )
        )
        db.add(
            CharacterTranslation(
                character_id=ch.id,
                locale="ru",
                name=c_spec["name_ru"],
                bio=c_spec["bio_ru"],
                appearance_notes=c_spec["appearance_ru"],
                abilities_summary=c_spec["abilities_ru"],
                spoiler_bio=c_spec["spoiler_ru"],
                source="community_verified",
            )
        )

        for idx, t_slug in enumerate(c_spec["traits"]):
            db.add(
                CharacterTrait(
                    character_id=ch.id,
                    trait_id=traits_by_slug[t_slug].id,
                    votes_count=42 - idx * 5,
                )
            )

        for m_slug, role, va, spoiler_unit in c_spec["links"]:
            db.add(
                MediaCharacter(
                    media_id=media_map[m_slug].id,
                    character_id=ch.id,
                    role=role,
                    voice_actor=va,
                    spoiler_after_unit=spoiler_unit,
                )
            )

    # Seed one sample list entry so "My Kiroku" feels alive immediately
    db.add(
        UserListEntry(
            profile_key="guest",
            media_id=media_map["solo-leveling-manhwa"].id,
            status="completed",
            progress_unit=200,
            score=9,
            is_favorite=True,
            notes="Top-tier art & Shadow Army progression.",
        )
    )
    db.add(
        UserListEntry(
            profile_key="guest",
            media_id=media_map["the-witcher-3-wild-hunt"].id,
            status="active",
            progress_unit=78,
            score=10,
            is_favorite=True,
            notes="Currently finishing Blood and Wine DLC.",
        )
    )

    db.commit()
    seed_extra_batch(db)


def seed_extra_batch(db: Session) -> None:
    """Idempotently add expanded traits, 10 new cross-media titles, and 10 iconic characters."""
    extra_traits = [
        ("red-hair", "appearance", "Crimson / Red Hair", "Красные / рыжие волосы"),
        ("pink-hair", "appearance", "Pink / Pastel Hair", "Розовые / пастельные волосы"),
        ("purple-hair", "appearance", "Violet / Purple Hair", "Фиолетовые волосы"),
        ("brown-hair", "appearance", "Brown Hair", "Каштановые волосы"),
        ("green-eyes", "appearance", "Emerald Green Eyes", "Изумрудные глаза"),
        ("red-eyes", "appearance", "Crimson Eyes", "Алые глаза"),
        ("amber-eyes", "appearance", "Golden / Amber Eyes", "Янтарные / золотые глаза"),
        ("glasses", "appearance", "Glasses", "Очки"),
        ("suit-uniform", "appearance", "Sharp Suit / Uniform", "Деловой костюм / форма"),
        ("pointy-ears", "appearance", "Elf / Pointy Ears", "Эльфийские уши"),
        ("chainsaw-devil", "ability", "Devil Contract / Control", "Контракт с демоном / контроль"),
        ("ancient-magic", "ability", "Ancient Elemental Magic", "Древняя магия и заклинания"),
        ("cyberware", "ability", "Netrunner / Cyberware", "Нетраннинг и киберимпланты"),
        ("deduction-genius", "ability", "Master Strategist & Detective", "Гениальный детектив / стратег"),
        ("persona-summon", "ability", "Persona / Cognitive Thief", "Призыв Персоны / Фантомный вор"),
        ("manipulator", "personality", "Master Manipulator", "Холодный манипулятор"),
        ("kuudere", "personality", "Calm & Stoic (Kuudere)", "Хладнокровный (Кудере)"),
        ("chaotic-good", "personality", "Rebellious Trickster", "Бунтарь / трикстер"),
        ("perfectionist", "personality", "Disciplined Perfectionist", "Дисциплинированный перфекционист"),
        ("sweet-tooth", "personality", "Quirky / Sweet Tooth", "Эксцентричный сладкоежка"),
    ]
    traits_map: dict[str, Trait] = {
        tr.slug: tr for tr in db.scalars(select(Trait)).all()
    }
    for slug, cat, n_en, n_ru in extra_traits:
        if slug not in traits_map:
            tr = Trait(slug=slug, category=cat, name_en=n_en, name_ru=n_ru)
            db.add(tr)
            traits_map[slug] = tr
    db.flush()

    extra_franchises = [
        ("frieren", "Frieren", "Атмосферное фэнтези о бессмертной эльфийке-маге и ценности времени.", "Atmospheric fantasy about an immortal elf mage learning the value of fleeting human lives."),
        ("chainsaw-man", "Chainsaw Man", "Безумное тёмное фэнтези Тацуки Фудзимото об охотниках на демонов.", "Tatsuki Fujimoto's visceral dark fantasy of devil hunters and contracts."),
        ("omniscient-reader", "Omniscient Reader's Viewpoint", "Культовая корейская веб-новелла и манхва о мире, превратившемся в роман о выживании.", "Iconic Korean webtoon where a lone reader's favorite apocalypse novel becomes reality."),
        ("tower-of-god", "Tower of God", "Масштабный корейский вебтун о загадочной Башне, исполняющей любые желания.", "Expansive Korean webtoon following climbers ascending a mysterious Tower."),
        ("cyberpunk", "Cyberpunk", "Неоновая антиутопия Найт-Сити о наёмниках и киберпсихозе.", "Neon dystopia of Night City mercenaries, netrunners, and corporate warfare."),
        ("elden-ring", "Elden Ring", "Эпическая тёмная RPG от FromSoftware и Джорджа Мартина о Междуземье.", "FromSoftware's dark fantasy action RPG set across the shattered Lands Between."),
        ("persona-5", "Persona 5", "Стильная JRPG о Призрачных Похитителях Сердец в современном Токио.", "Stylish JRPG following the Phantom Thieves of Hearts in modern Tokyo."),
        ("attack-on-titan", "Attack on Titan", "Мрачная сага о стенах, титанах и цене свободы человечества.", "Grim saga of humanity behind giant walls fighting Titans for survival."),
        ("clannad", "Clannad", "Знаковая драматическая визуальная новелла студии Key о семье и втором шансе.", "Key's celebrated dramatic visual novel about family, loss, and miracles."),
        ("death-note", "Death Note", "Интеллектуальная дуэль между гениальным школьником и величайшим детективом мира.", "Psychological cat-and-mouse duel between Light Yagami and L."),
    ]
    fran_map: dict[str, Franchise] = {
        fr.slug: fr for fr in db.scalars(select(Franchise)).all()
    }
    for slug, name, d_ru, d_en in extra_franchises:
        if slug not in fran_map:
            fr = Franchise(slug=slug, canonical_name=name, description_ru=d_ru, description_en=d_en)
            db.add(fr)
            fran_map[slug] = fr
    db.flush()

    new_media_specs = [
        dict(
            slug="frieren-anime",
            franchise="frieren",
            media_type="anime",
            canonical_title="Frieren: Beyond Journey's End",
            native_title="葬送のフリーレン",
            release_year=2023,
            status="completed",
            rating=9.3,
            popularity=108000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx154587-qQTzQnEJJ3oB.jpg",
            total_units=28,
            unit_label="ep",
            genres=["Adventure", "Drama", "Fantasy"],
            platforms=["Crunchyroll"],
            title_ru="Провожающая в последний путь Фрирен",
            synopsis_en="After a ten-year quest to defeat the Demon King, the near-immortal elf mage Frieren embarks on a quiet journey to understand human hearts.",
            synopsis_ru="После десятилетнего похода и победы над Королём Демонов почти бессмертная эльфийка Фрирен отправляется в новое путешествие, чтобы понять ценность человеческих жизней.",
            provider="anilist",
            ext_id="154587",
        ),
        dict(
            slug="chainsaw-man-manga",
            franchise="chainsaw-man",
            media_type="manga",
            canonical_title="Chainsaw Man",
            native_title="チェンソーマン",
            release_year=2018,
            status="ongoing",
            rating=8.8,
            popularity=102000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx105778-euxXZEIfDY2u.png",
            total_units=180,
            unit_label="ch",
            genres=["Action", "Dark Fantasy", "Horror"],
            platforms=["Shonen Jump+"],
            title_ru="Человек-бензопила (Манга)",
            synopsis_en="Debt-ridden Denji merges with his chainsaw devil Pochita and is recruited into Public Safety by the enigmatic Makima.",
            synopsis_ru="Погрязший в долгах Дэндзи сливается с демоном-бензопилой Почитой и попадает в Бюро общественной безопасности под начало загадочной Макимы.",
            provider="anilist",
            ext_id="105778",
        ),
        dict(
            slug="omniscient-reader-manhwa",
            franchise="omniscient-reader",
            media_type="manhwa",
            canonical_title="Omniscient Reader's Viewpoint",
            native_title="전지적 독자 시점",
            release_year=2020,
            status="ongoing",
            rating=9.1,
            popularity=97000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx119257-Pi21aq3ey9GG.jpg",
            total_units=235,
            unit_label="ch",
            genres=["Action", "Fantasy", "Apocalypse"],
            platforms=["Naver Webtoon"],
            title_ru="Всеведущий читатель (Манхва)",
            synopsis_en="Ordinary office worker Kim Dokja is the only person who read a web novel to its end—until the novel's brutal scenarios become reality.",
            synopsis_ru="Обычный офисный работник Ким Докча — единственный, кто дочитал веб-роман о конце света до финала, и в тот же день его сюжет стал реальностью.",
            provider="anilist",
            ext_id="119257",
        ),
        dict(
            slug="tower-of-god-manhwa",
            franchise="tower-of-god",
            media_type="manhwa",
            canonical_title="Tower of God",
            native_title="신의 탑",
            release_year=2010,
            status="ongoing",
            rating=8.6,
            popularity=88000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx85143-23oup3ETbFJk.jpg",
            total_units=640,
            unit_label="ch",
            genres=["Action", "Adventure", "Mystery"],
            platforms=["Naver Webtoon"],
            title_ru="Башня Бога (Манхва)",
            synopsis_en="Twenty-Fifth Baam enters a colossal Tower where wealth, power, and truth await at the summit—climbing alongside strategist Khun.",
            synopsis_ru="Двадцать Пятый Баам входит в загадочную Башню, где на вершине ждёт исполнение любого желания, и объединяется со стратегом Куном.",
            provider="anilist",
            ext_id="85143",
        ),
        dict(
            slug="cyberpunk-edgerunners-anime",
            franchise="cyberpunk",
            media_type="anime",
            canonical_title="Cyberpunk: Edgerunners",
            native_title="サイバーパンク エッジランナーズ",
            release_year=2022,
            status="completed",
            rating=8.7,
            popularity=95000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx120377-ayZPoxiWt4Li.jpg",
            total_units=10,
            unit_label="ep",
            genres=["Sci-Fi", "Action", "Cyberpunk"],
            platforms=["Netflix"],
            title_ru="Киберпанк: Бегущие по краю",
            synopsis_en="In dystopian Night City, street kid David Martinez becomes an edgerunner mercenary alongside skilled netrunner Lucy.",
            synopsis_ru="В жестоком Найт-Сити уличный парень Дэвид Мартинес становится наёмником-бегущим по краю вместе с талантливой нетраннершей Люси.",
            provider="anilist",
            ext_id="120377",
        ),
        dict(
            slug="elden-ring-game",
            franchise="elden-ring",
            media_type="game",
            canonical_title="Elden Ring",
            native_title="エルデンリング",
            release_year=2022,
            status="completed",
            rating=9.6,
            popularity=120000,
            cover_url="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/1245620/library_600x900_2x.jpg",
            total_units=110,
            unit_label="hr",
            genres=["Action RPG", "Soulslike", "Dark Fantasy"],
            platforms=["PC", "PS5", "Xbox"],
            title_ru="Elden Ring (Игра)",
            synopsis_en="Rise, Tarnished, and be guided by grace to brandish the power of the Elden Ring and become an Elden Lord in the Lands Between.",
            synopsis_ru="Восстань, Погасший, чтобы пройти через расколотое Междуземье, одолеть полубогов и стать Повелителем Элдена.",
            provider="igdb",
            ext_id="119133",
        ),
        dict(
            slug="persona-5-royal-game",
            franchise="persona-5",
            media_type="game",
            canonical_title="Persona 5 Royal",
            native_title="ペルソナ5 ザ・ロイヤル",
            release_year=2019,
            status="completed",
            rating=9.4,
            popularity=99000,
            cover_url="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/1687950/library_600x900_2x.jpg",
            total_units=105,
            unit_label="hr",
            genres=["JRPG", "Turn-Based", "Urban Fantasy"],
            platforms=["PC", "PS5", "Switch", "Xbox"],
            title_ru="Persona 5 Royal (Игра)",
            synopsis_en="Don the mask of Joker and lead the Phantom Thieves of Hearts to steal distorted desires from corrupt adults across Tokyo's Metaverse.",
            synopsis_ru="Примерь маску Джокера и возглавь Призрачных Похитителей Сердец, чтобы очистить искажённые желания коррумпированных взрослых в Метавселенной Токио.",
            provider="igdb",
            ext_id="114283",
        ),
        dict(
            slug="attack-on-titan-anime",
            franchise="attack-on-titan",
            media_type="anime",
            canonical_title="Attack on Titan",
            native_title="進撃の巨人",
            release_year=2013,
            status="completed",
            rating=9.0,
            popularity=125000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx16498-buvcRTBx4NSm.jpg",
            total_units=89,
            unit_label="ep",
            genres=["Action", "Drama", "Dark Fantasy"],
            platforms=["Crunchyroll"],
            title_ru="Атака титанов",
            synopsis_en="After Titans breach Wall Maria, humanity's last survivors join the Survey Corps under humanity's strongest soldier, Captain Levi.",
            synopsis_ru="После падения стены Мария остатки человечества вступают в Разведкорпус, где сражаются против титанов под началом капитана Леви.",
            provider="anilist",
            ext_id="16498",
        ),
        dict(
            slug="clannad-vn",
            franchise="clannad",
            media_type="vn",
            canonical_title="Clannad",
            native_title="CLANNAD",
            release_year=2004,
            status="completed",
            rating=9.1,
            popularity=71000,
            cover_url="https://t.vndb.org/cv/95/75895.jpg",
            total_units=60,
            unit_label="hr",
            genres=["Drama", "Romance", "Slice of Life"],
            platforms=["PC", "Switch", "PS4"],
            title_ru="Кланнад (Визуальная новелла)",
            synopsis_en="Delinquent Tomoya Okazaki meets frail Nagisa Furukawa and helps revive her school drama club while healing broken family bonds.",
            synopsis_ru="Школьник Томоя Окадзаки знакомится с застенчивой Нагисой Фурукавой и помогает ей возродить театральный кружок, обретая смысл жизни.",
            provider="vndb",
            ext_id="v4",
        ),
        dict(
            slug="death-note-anime",
            franchise="death-note",
            media_type="anime",
            canonical_title="Death Note",
            native_title="デスノート",
            release_year=2006,
            status="completed",
            rating=8.9,
            popularity=118000,
            cover_url="https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1535-kUgkcrfOrkUM.jpg",
            total_units=37,
            unit_label="ep",
            genres=["Psychological", "Thriller", "Mystery"],
            platforms=["Crunchyroll"],
            title_ru="Тетрадь смерти",
            synopsis_en="Genius student Light Yagami finds a notebook that kills anyone whose name is written inside, drawing the attention of legendary detective L.",
            synopsis_ru="Гениальный школьник Лайт Ягами находит тетрадь бога смерти и начинает чистку мира, сталкиваясь с легендарным детективом L.",
            provider="anilist",
            ext_id="1535",
        ),
    ]

    media_map: dict[str, Media] = {m.slug: m for m in db.scalars(select(Media)).all()}
    for spec in new_media_specs:
        if spec["slug"] in media_map:
            continue
        m = Media(
            slug=spec["slug"],
            franchise_id=fran_map[spec["franchise"]].id,
            media_type=spec["media_type"],
            canonical_title=spec["canonical_title"],
            native_title=spec["native_title"],
            release_year=spec["release_year"],
            status=spec["status"],
            rating=spec["rating"],
            popularity=spec["popularity"],
            cover_url=spec["cover_url"],
            total_units=spec["total_units"],
            unit_label=spec["unit_label"],
            genres=spec["genres"],
            platforms=spec["platforms"],
        )
        db.add(m)
        db.flush()
        db.add(MediaTranslation(media_id=m.id, locale="en", title=spec["canonical_title"], synopsis=spec["synopsis_en"], source="community_verified"))
        db.add(MediaTranslation(media_id=m.id, locale="ru", title=spec["title_ru"], synopsis=spec["synopsis_ru"], source="community_verified"))
        db.add(ExternalMapping(entity_type="media", entity_id=m.id, provider=spec["provider"], external_id=spec["ext_id"]))
        media_map[spec["slug"]] = m

    new_char_specs = [
        dict(
            slug="frieren",
            franchise="frieren",
            canonical_name="Frieren",
            native_name="フリーレン",
            name_ru="Фрирен",
            gender="female",
            hair_color="white",
            eye_color="green",
            hair_length="long",
            mbti="INTP",
            enneagram="9w1",
            archetype="Immortal Elf Mage",
            birthday="Unknown",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b176754-PCnpqIOkjhFk.png",
            popularity=104000,
            bio_en="An ancient elf mage who helped defeat the Demon King and now travels with Fern and Stark to collect spells and revisit memories of Himmel.",
            bio_ru="Древняя эльфийка-маг из отряда героев, одолевших Короля Демонов. Спустя десятилетия отправляется в новый путь, чтобы лучше понимать людей.",
            appearance_ru="Миниатюрная эльфийка с длинными серебристо-белыми волосами в двух хвостиках, зелёными глазами и золотыми серьгами.",
            abilities_ru="Магия подавления ауры, Зольтраак (магия убийства душ), древние заклинания и тысячи лет боевого опыта.",
            spoiler_ru="Настоящее прозвище Фрирен среди демонов — «Фрирен-Убийца»: она столетиями скрывала свою истинную чудовищную ману, чтобы заманивать демонов в ловушку.",
            traits=["white-hair", "green-eyes", "pointy-ears", "ancient-magic", "kuudere"],
            links=[("frieren-anime", "main", "Atsumi Tanezaki", None)],
        ),
        dict(
            slug="makima",
            franchise="chainsaw-man",
            canonical_name="Makima",
            native_name="マキマ",
            name_ru="Макима",
            gender="female",
            hair_color="red",
            eye_color="amber",
            hair_length="long",
            mbti="INFJ",
            enneagram="1w9",
            archetype="Control Devil",
            birthday="Unknown",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b137080-UHcynYNjb5ZU.png",
            popularity=98000,
            bio_en="High-ranking Public Safety Devil Hunter who commands absolute loyalty from Denji while hiding terrifying motives behind a calm smile.",
            bio_ru="Высокопоставленный охотник на демонов из Бюро общественной безопасности. Держит Дэндзи под полным контролем за мягкой и безупречной улыбкой.",
            appearance_ru="Девушка с длинными рыже-алыми волосами, заплетенными в косу, золотыми глазами с концентрическими кольцами и строгим деловым костюмом.",
            abilities_ru="Подчинение любого, кто считает себя ниже её, передача смертельных ран гражданам Японии по контракту, дистанционное сокрушение.",
            spoiler_ru="Макима — воплощение Демона Контроля (одного из Четырёх Всадников Апокалипсиса), мечтавшая использовать Человека-бензопилу, чтобы стереть смерть, войну и голод.",
            traits=["red-hair", "amber-eyes", "suit-uniform", "chainsaw-devil", "manipulator"],
            links=[("chainsaw-man-manga", "antagonist", "Tomori Kusunoki", None)],
        ),
        dict(
            slug="kim-dokja",
            franchise="omniscient-reader",
            canonical_name="Kim Dokja",
            native_name="김독자",
            name_ru="Ким Докча",
            gender="male",
            hair_color="black",
            eye_color="black",
            hair_length="short",
            mbti="INTP",
            enneagram="5w4",
            archetype="Demon King of Salvation",
            birthday="February 15",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b180887-s9sJWFuO18J8.png",
            popularity=95000,
            bio_en="The sole reader who finished 'Three Ways to Survive in a Ruined World', using meta-knowledge of every scenario to outsmart constellations.",
            bio_ru="Единственный читатель, дочитавший веб-роман о выживании до конца. Использует знание будущего, чтобы переигрывать богов-созвездий и спасать спутников.",
            appearance_ru="Стройный молодой мужчина с чёрными волосами, усталыми тёмными глазами и белым пальто в сценариях.",
            abilities_ru="Четвёртая Стена (иммунитет к ментальному шоку), Закладка (копирование навыков персонажей романа), Неразрушимая Вера.",
            spoiler_ru="Модификатор Ким Докчи — «Король Демонов Спасения», а в финале раскрывается, что сам он является «Древнейшей Мечтой», чьё чтение поддерживает существование всей вселенной.",
            traits=["black-hair", "suit-uniform", "deduction-genius", "antihero"],
            links=[("omniscient-reader-manhwa", "main", "Webtoon Protagonist", None)],
        ),
        dict(
            slug="khun-aguero-agnis",
            franchise="tower-of-god",
            canonical_name="Khun Aguero Agnis",
            native_name="쿤 아게로 아그니스",
            name_ru="Кун Агеро Агнис",
            gender="male",
            hair_color="blue",
            eye_color="blue",
            hair_length="short",
            mbti="ENTJ",
            enneagram="3w4",
            archetype="Cunning Light Bearer",
            birthday="November 29",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b84781-sNnhOZ1SZ66f.png",
            popularity=87000,
            bio_en="An exiled son of the Great Khun Family who relies on sharp intellect, Lighthouses, and ruthless schemes to protect Baam.",
            bio_ru="Изгнанник из великой семьи Кун. Полагается на блестящий ум, Светочи и многоходовые интриги, чтобы защищать Баама в Башне.",
            appearance_ru="Юноша со светло-голубыми волосами, проницательными синими глазами, тёмной серёжкой и фирменной повязкой на волосах.",
            abilities_ru="Управление Светочами (разведка и барьеры), бездонная сумка Манбаронден, ледяное копьё и огненная рыба.",
            spoiler_ru="После покушения со стороны Рахиль Кун долгое время находился в крио-сне и пробудился благодаря пламени древней огненной рыбы, давшей ему силы целителя и поддержки.",
            traits=["blue-eyes", "manipulator", "deduction-genius"],
            links=[("tower-of-god-manhwa", "main", "Nobuhiko Okamoto", None)],
        ),
        dict(
            slug="lucy-edgerunners",
            franchise="cyberpunk",
            canonical_name="Lucy (Lucyna Kushinada)",
            native_name="ルーシー",
            name_ru="Люси (Люцина Кушинада)",
            gender="female",
            hair_color="white",
            eye_color="pink",
            hair_length="medium",
            mbti="INFJ",
            enneagram="4w5",
            archetype="Moon-Dreaming Netrunner",
            birthday="Unknown",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b284157-cqYawN7XCNJx.jpg",
            popularity=91000,
            bio_en="An elusive elite netrunner born to break Arasaka ICE, dreaming of leaving the neon bloodshed of Night City for the Moon.",
            bio_ru="Талантливая нетраннерша, сбежавшая из секретной лаборатории Арасаки и мечтающая вырваться из кровавого Найт-Сити на Луну.",
            appearance_ru="Асимметричное каре с белыми и пастельно-радужными прядями, неоново-розовые глаза, белый боди-костюм и чёрный жакет.",
            abilities_ru="Мастер взлома и глубокого погружения в Сеть, моноструна в запястье, скрытие цифровых следов.",
            spoiler_ru="После гибели Дэвида в Арасака-Тауэр Люси исполняет их общую мечту и добирается до Луны в одиночку.",
            traits=["white-hair", "cyberware", "kuudere"],
            links=[("cyberpunk-edgerunners-anime", "main", "Aoi Yuuki", None)],
        ),
        dict(
            slug="malenia-blade-of-miquella",
            franchise="elden-ring",
            canonical_name="Malenia, Blade of Miquella",
            native_name="マレニア",
            name_ru="Маления, Клинок Микеллы",
            gender="female",
            hair_color="red",
            eye_color="unknown",
            hair_length="long",
            mbti="ISTJ",
            enneagram="1w9",
            archetype="Undefeated Demigoddess",
            birthday="Unknown",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b299765-hRQoXwscGpjJ.jpg",
            popularity=94000,
            bio_en="Empyrean demigoddess cursed with Scarlet Rot from birth, famed across the Lands Between as an undefeated swordswoman.",
            bio_ru="Полубогиня Междуземья, с рождения проклятая Красной Гнилью, но никогда не знавшая поражения в бою на мечах.",
            appearance_ru="Высокая воительница с длинными рыжими волосами, крылатым золотым шлемом, закрывающим глаза, и золотыми протезами.",
            abilities_ru="Водный танец клинка (Waterfowl Dance), вампиризм ударов, расцвет Алой Эонии (Красная Гниль).",
            spoiler_ru="Во второй фазе битвы Маления сбрасывает сдержанность Микеллы и возрождается как Богиня Гнили.",
            traits=["red-hair", "swordsmanship", "scar", "perfectionist"],
            links=[("elden-ring-game", "antagonist", "Pip Torrens / Game Boss", None)],
        ),
        dict(
            slug="ren-amamiya-joker",
            franchise="persona-5",
            canonical_name="Joker (Ren Amamiya)",
            native_name="雨宮蓮",
            name_ru="Джокер (Рэн Амамия)",
            gender="male",
            hair_color="black",
            eye_color="grey",
            hair_length="short",
            mbti="INFJ",
            enneagram="5w4",
            archetype="Phantom Thief Leader",
            birthday="Unknown",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b121590-0IyliPlkGSZa.png",
            popularity=93000,
            bio_en="Falsely accused high school transfer student who awakens the Wild Card power and leads the Phantom Thieves as Joker.",
            bio_ru="Несправедливо осуждённый старшеклассник, пробудивший силу Дикой Карты и возглавивший Призрачных Похитителей под маской Джокера.",
            appearance_ru="Растрёпанные чёрные волосы, круглые очки в обычной жизни, чёрный плащ, белая маска и алые перчатки в Метавселенной.",
            abilities_ru="Персона Арсен и Сатанаэль, Дикая Карта (смена десятков Персон), тактическая акробатика и пистолет/нож.",
            spoiler_ru="Джокер инсценирует собственную гибель в комнате допроса с помощью метавселенной Саэ, чтобы разоблачить предателя Акети и Сидо.",
            traits=["black-hair", "glasses", "suit-uniform", "persona-summon", "chaotic-good"],
            links=[("persona-5-royal-game", "main", "Jun Fukuyama", None)],
        ),
        dict(
            slug="levi-ackerman",
            franchise="attack-on-titan",
            canonical_name="Levi Ackerman",
            native_name="リヴァイ",
            name_ru="Леви Аккерман",
            gender="male",
            hair_color="black",
            eye_color="grey",
            hair_length="short",
            mbti="ISTP",
            enneagram="8w9",
            archetype="Humanity's Strongest Soldier",
            birthday="December 25",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b45627-CR68RyZmddGG.png",
            popularity=112000,
            bio_en="Captain of the Special Operations Squad in the Survey Corps, feared for his unmatched ODM combat skill and obsession with cleanliness.",
            bio_ru="Капитан особого отряда Разведкорпуса, признанный сильнейшим воином человечества. За суровой сдержанностью и чистоплотностью скрывает боль утрат.",
            appearance_ru="Невысокий мужчина с угольно-чёрными волосами с подбритым затылком, стальными серыми глазами и белым шейным платком.",
            abilities_ru="Молниеносное вращение на УПМ, пробуждённая сила рода Аккерман, клинки против титанов.",
            spoiler_ru="После взрыва Громового Копья Зика Леви теряет два пальца и получает тяжёлые шрамы, но всё равно исполняет обещание Эрвину и убивает Звероподобного.",
            traits=["black-hair", "scar", "swordsmanship", "perfectionist", "kuudere"],
            links=[("attack-on-titan-anime", "main", "Hiroshi Kamiya", None)],
        ),
        dict(
            slug="nagisa-furukawa",
            franchise="clannad",
            canonical_name="Nagisa Furukawa",
            native_name="古河 渚",
            name_ru="Нагиса Фурукава",
            gender="female",
            hair_color="brown",
            eye_color="amber",
            hair_length="medium",
            mbti="ISFJ",
            enneagram="9w1",
            archetype="Gentle Heart",
            birthday="December 24",
            image_url="https://t.vndb.org/ch/28/27928.jpg",
            popularity=76000,
            bio_en="Warm-hearted student repeating her senior year due to fragile health, determined to reopen the school's drama club.",
            bio_ru="Добрая и искренне верящая в людей девушка, оставшаяся на второй год из-за слабого здоровья и мечтающая возродить школьный театральный кружок.",
            appearance_ru="Каштановые волосы с характерными двумя торчащими прядями (ахогэ), тёплые янтарные глаза и школьная форма Хикаридзаки.",
            abilities_ru="Стойкость духа, актёрский талант и умение объединять людей вокруг «Большой семьи Данго».",
            spoiler_ru="В арке After Story Нагиса умирает при родах дочери Усио, но благодаря собранным огням чудес Томоя возвращается во времени в мир, где она выживает.",
            traits=["brown-hair", "amber-eyes"],
            links=[("clannad-vn", "main", "Mai Nakahara", None)],
        ),
        dict(
            slug="l-lawliet",
            franchise="death-note",
            canonical_name="L Lawliet",
            native_name="エル・ローライト",
            name_ru="L (Эл Лоулайт)",
            gender="male",
            hair_color="black",
            eye_color="black",
            hair_length="short",
            mbti="INTP",
            enneagram="5w4",
            archetype="World's Greatest Detective",
            birthday="October 31",
            image_url="https://s4.anilist.co/file/anilistcdn/character/large/b71-1W4panC53vfs.png",
            popularity=115000,
            bio_en="Anonymous genius detective who corners Kira using daring psychological traps, unconventional posture, and endless sugar.",
            bio_ru="Таинственный величайший детектив мира, ведущий интеллектуальную войну против Киры при помощи смелых ловушек и бесконечных сладостей.",
            appearance_ru="Растрёпанные чёрные волосы, бледная кожа, тёмные круги под большими глазами, простая белая футболка и джинсы.",
            abilities_ru="Дедукция мирового уровня, психологическое профилирование, владение капоэйрой.",
            spoiler_ru="L вплотную приближается к доказательству вины Лайта, но погибает от руки синигами Рэм, которую Лайт шантажом вынуждает защитить Мису.",
            traits=["black-hair", "deduction-genius", "sweet-tooth", "chaotic-good"],
            links=[("death-note-anime", "main", "Kappei Yamaguchi", None)],
        ),
    ]

    existing_chars = {c.slug: c for c in db.scalars(select(Character)).all()}
    for c_spec in new_char_specs:
        if c_spec["slug"] in existing_chars:
            continue
        ch = Character(
            slug=c_spec["slug"],
            franchise_id=fran_map[c_spec["franchise"]].id,
            canonical_name=c_spec["canonical_name"],
            native_name=c_spec["native_name"],
            gender=c_spec["gender"],
            hair_color=c_spec["hair_color"],
            eye_color=c_spec["eye_color"],
            hair_length=c_spec["hair_length"],
            mbti=c_spec["mbti"],
            enneagram=c_spec["enneagram"],
            archetype=c_spec["archetype"],
            birthday=c_spec["birthday"],
            image_url=c_spec["image_url"],
            popularity=c_spec["popularity"],
        )
        db.add(ch)
        db.flush()
        db.add(CharacterTranslation(
            character_id=ch.id, locale="en", name=c_spec["canonical_name"], bio=c_spec["bio_en"],
            appearance_notes=f"{c_spec['hair_color'].title()} hair, {c_spec['eye_color']} eyes.",
            abilities_summary=c_spec["archetype"],
            spoiler_bio="Major late-story revelation protected by Kiroku Anti-Spoiler Guard.",
            source="community_verified",
        ))
        db.add(CharacterTranslation(
            character_id=ch.id, locale="ru", name=c_spec["name_ru"], bio=c_spec["bio_ru"],
            appearance_notes=c_spec["appearance_ru"],
            abilities_summary=c_spec["abilities_ru"],
            spoiler_bio=c_spec["spoiler_ru"],
            source="community_verified",
        ))
        for idx, t_slug in enumerate(c_spec["traits"]):
            if t_slug in traits_map:
                db.add(CharacterTrait(character_id=ch.id, trait_id=traits_map[t_slug].id, votes_count=55 - idx * 4))
        for m_slug, role, va, sp_unit in c_spec["links"]:
            if m_slug in media_map:
                db.add(MediaCharacter(media_id=media_map[m_slug].id, character_id=ch.id, role=role, voice_actor=va, spoiler_after_unit=sp_unit))

    db.commit()
