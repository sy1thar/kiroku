import re

UI_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "brand_tagline": "Глобальная кросс-медиа энциклопедия",
        "nav_home": "Главная",
        "nav_catalog": "Каталог",
        "nav_characters": "Персонажи",
        "nav_import": "Импорт API",
        "nav_moderation": "Вики-правки",
        "nav_lists": "Мой Kiroku",
        "nav_about": "О проекте",
        "nav_login": "Войти",
        "nav_register": "Регистрация",
        "nav_logout": "Выйти",
        "search_placeholder": "Поиск тайтлов и персонажей...",
        "char_search_placeholder": "Имя или архетип персонажа...",
        "char_search_label": "Поиск персонажа",
        "search_hint": "Нажмите / или Ctrl+K для быстрого поиска",
        "hero_title": "Все миры и герои в одной хронике",
        "hero_subtitle": "Аниме, манга, манхва, видеоигры и визуальные новеллы. Кросс-медиа граф персонажей, поиск по внешности и психотипам, защита от спойлеров и вики-редактор.",
        "explore_media": "Открыть каталог",
        "find_character": "Найти персонажа",
        "trending_media": "Популярные тайтлы",
        "featured_characters": "Культовые персонажи",
        "franchises_title": "Кросс-медиа вселенные",
        "all_types": "Все форматы",
        "filter_apply": "Применить",
        "filter_reset": "Сбросить",
        "hair_color": "Цвет волос",
        "eye_color": "Цвет глаз",
        "gender": "Пол",
        "mbti": "Психотип (MBTI)",
        "trait": "Тег / черта",
        "medium": "Медиум",
        "synopsis": "Описание",
        "cast_title": "Персонажи тайтла",
        "franchise_timeline": "Другие тайтлы вселенной",
        "appearances": "Появления в медиа",
        "bio": "История и характер",
        "abilities": "Способности и стиль",
        "spoiler_section": "Сюжетные спойлеры",
        "spoiler_toggle": "Показать спойлеры",
        "spoiler_hide": "Скрыть спойлеры",
        "spoiler_guard_note": "Скрыто защитой от спойлеров. Укажите ваш прогресс или откройте вручную.",
        "suggest_edit": "Предложить правку (Wiki)",
        "propose_btn": "Отправить на ревью",
        "traits_title": "Теги внешности, характера и сил",
        "vote_hint": "Нажмите на тег, чтобы проголосовать",
        "tracker_title": "Мой трекер Kiroku",
        "save_tracker": "Сохранить в Kiroku",
        "no_results": "Ничего не найдено по заданным фильтрам.",
        "import_title": "Импорт из глобальных баз (AniList, VNDB, IGDB)",
        "import_subtitle": "Загрузите любой тайтл и его персонажей напрямую в локальную базу Kiroku (Lazy Import).",
        "moderation_title": "Очередь вики-правок",
        "approve": "Принять",
        "reject": "Отклонить",
        "pending_empty": "Все предложенные правки уже проверены.",
    },
    "en": {
        "brand_tagline": "Global Cross-Media & Character Chronicle",
        "nav_home": "Home",
        "nav_catalog": "Catalog",
        "nav_characters": "Characters",
        "nav_import": "API Import",
        "nav_moderation": "Wiki Queue",
        "nav_lists": "My Kiroku",
        "nav_about": "About",
        "nav_login": "Sign In",
        "nav_register": "Register",
        "nav_logout": "Log Out",
        "search_placeholder": "Search titles & characters...",
        "char_search_placeholder": "Character name or archetype...",
        "char_search_label": "Find character",
        "search_hint": "Press / or Ctrl+K to search anywhere",
        "hero_title": "Every world and character in one chronicle",
        "hero_subtitle": "Anime, manga, manhwa, video games, and visual novels. Cross-media character graph, appearance & MBTI finder, anti-spoiler guard, and community wiki.",
        "explore_media": "Explore Catalog",
        "find_character": "Find Character",
        "trending_media": "Trending Titles",
        "featured_characters": "Iconic Characters",
        "franchises_title": "Cross-Media Franchises",
        "all_types": "All Formats",
        "filter_apply": "Apply",
        "filter_reset": "Reset",
        "hair_color": "Hair Color",
        "eye_color": "Eye Color",
        "gender": "Gender",
        "mbti": "Personality (MBTI)",
        "trait": "Trait / Tag",
        "medium": "Medium",
        "synopsis": "Synopsis",
        "cast_title": "Cast & Characters",
        "franchise_timeline": "More in this Universe",
        "appearances": "Cross-Media Appearances",
        "bio": "Lore & Personality",
        "abilities": "Abilities & Combat Style",
        "spoiler_section": "Story Spoilers",
        "spoiler_toggle": "Reveal Spoilers",
        "spoiler_hide": "Hide Spoilers",
        "spoiler_guard_note": "Protected by Kiroku Anti-Spoiler Guard. Reveal when ready.",
        "suggest_edit": "Suggest Edit (Wiki)",
        "propose_btn": "Submit Revision",
        "traits_title": "Appearance, Personality & Ability Tags",
        "vote_hint": "Click any tag to upvote",
        "tracker_title": "My Kiroku Tracker",
        "save_tracker": "Save to Kiroku",
        "no_results": "No entries matched your filters.",
        "import_title": "Live Global API Import (AniList, VNDB, IGDB)",
        "import_subtitle": "Pull any title and its characters straight into your Kiroku database on demand.",
        "moderation_title": "Wiki Revision Moderation",
        "approve": "Approve",
        "reject": "Reject",
        "pending_empty": "No pending revisions right now.",
    },
}

MEDIA_TYPE_LABELS: dict[str, dict[str, str]] = {
    "anime": {"ru": "Аниме", "en": "Anime"},
    "manga": {"ru": "Манга", "en": "Manga"},
    "manhwa": {"ru": "Манхва", "en": "Manhwa"},
    "manhua": {"ru": "Маньхуа", "en": "Manhua"},
    "novel": {"ru": "Ранобэ / Книга", "en": "Novel"},
    "game": {"ru": "Видеоигра", "en": "Video Game"},
    "vn": {"ru": "Визуальная новелла", "en": "Visual Novel"},
    "series": {"ru": "Сериал / Анимация", "en": "Series"},
}

COLOR_LABELS: dict[str, dict[str, str]] = {
    "black": {"ru": "Чёрные", "en": "Black"},
    "white": {"ru": "Белые / Серебряные", "en": "White / Silver"},
    "blue": {"ru": "Синие / Голубые", "en": "Blue"},
    "red": {"ru": "Красные", "en": "Red"},
    "blonde": {"ru": "Блонд / Золотые", "en": "Blonde / Gold"},
    "brown": {"ru": "Каштановые / Карие", "en": "Brown"},
    "green": {"ru": "Зелёные", "en": "Green"},
    "pink": {"ru": "Розовые", "en": "Pink"},
    "purple": {"ru": "Фиолетовые", "en": "Purple"},
    "amber": {"ru": "Янтарные / Жёлтые", "en": "Amber / Yellow"},
    "grey": {"ru": "Серые", "en": "Grey"},
    "unknown": {"ru": "Не указано", "en": "Unknown"},
}

ROLE_LABELS: dict[str, dict[str, str]] = {
    "main": {"ru": "Главный герой", "en": "Main"},
    "supporting": {"ru": "Второстепенный", "en": "Supporting"},
    "antagonist": {"ru": "Антагонист", "en": "Antagonist"},
}

SOURCE_BADGES: dict[str, dict[str, str]] = {
    "community_verified": {"ru": "Проверено сообществом", "en": "Community Verified"},
    "ai_draft": {"ru": "AI-перевод (можно улучшить)", "en": "AI Draft (Editable)"},
    "api_raw": {"ru": "Данные API", "en": "API Source"},
}

LIST_STATUS_LABELS: dict[str, dict[str, str]] = {
    "active": {"ru": "В процессе", "en": "Active"},
    "completed": {"ru": "Завершено", "en": "Completed"},
    "planned": {"ru": "В планах", "en": "Planned"},
    "dropped": {"ru": "Брошено", "en": "Dropped"},
}

GENDER_LABELS: dict[str, dict[str, str]] = {
    "male": {"ru": "Мужской", "en": "Male"},
    "female": {"ru": "Женский", "en": "Female"},
}

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_KANA_TO_ROMAJI = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "wo", "ん": "n", "ー": "",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "っ": "",
}

_HANGUL_LEAD = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_HANGUL_VOWEL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_HANGUL_TAIL = ["", "k", "k", "k", "n", "n", "n", "t", "l", "l", "l", "l", "l", "l", "l", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]


def _katakana_to_hiragana(ch: str) -> str:
    code = ord(ch)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return ch


def _romanize_cjk_hangul(text: str) -> str:
    out: list[str] = []
    for raw_ch in text:
        code = ord(raw_ch)
        if 0xAC00 <= code <= 0xD7A3:
            idx = code - 0xAC00
            lead = idx // 588
            vowel = (idx % 588) // 28
            tail = idx % 28
            out.append(_HANGUL_LEAD[lead] + _HANGUL_VOWEL[vowel] + _HANGUL_TAIL[tail])
            continue
        hira = _katakana_to_hiragana(raw_ch)
        if hira in _KANA_TO_ROMAJI:
            out.append(_KANA_TO_ROMAJI[hira])
            continue
        out.append(_CYR_TO_LAT.get(raw_ch, raw_ch))
    return "".join(out)


def normalize_for_search(text: str) -> str:
    """Normalize text across Cyrillic, Latin, Japanese Kana, and Korean Hangul."""
    lowered = text.lower().strip()
    translit = _romanize_cjk_hangul(lowered)
    translit = translit.replace("dzh", "j").replace("dj", "j").replace("ou", "o").replace("uu", "u")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", " ", translit).strip()


def normalize_unicode_raw(text: str) -> str:
    """Preserve original scripts (CJK, Hangul, Cyrillic, Latin) with punctuation stripped."""
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def slugify(text: str) -> str:
    norm = normalize_for_search(text)
    ascii_only = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    return ascii_only[:120] or "item"


def t_ui(locale: str) -> dict[str, str]:
    return UI_STRINGS.get(locale, UI_STRINGS["en"])
