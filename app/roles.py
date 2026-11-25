"""
Таблица ролей пользователей в коде (Kiroku).
Здесь вы можете выдавать роли ("admin", "moderator", "user") по нику прямо в коде.
При запуске сайта, входе или регистрации роль из этой таблицы применяется автоматически.
"""

USER_ROLES_TABLE: dict[str, str] = {
    "Ikazych1": "admin",
    # Примеры:
    # "FriendNick": "moderator",
    # "AnotherUser": "user",
}
