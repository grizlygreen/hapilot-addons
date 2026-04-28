"""Domain + device_class → emoji + label classifiers."""

DOMAIN_ICON = {
    "light": "💡",
    "switch": "🔌",
    "fan": "🌪",
    "climate": "🌡",
    "cover": "🪟",
    "scene": "🎬",
    "script": "📜",
    "sensor": "📊",
    "binary_sensor": "🚨",
    "media_player": "📺",
    "humidifier": "💧",
    "vacuum": "🧹",
    "lock": "🔐",
    "input_boolean": "🎚",
    "automation": "⚙",
    "person": "👤",
    "camera": "📷",
}

DOMAIN_LABEL = {
    "light": "Свет",
    "switch": "Выключатели",
    "fan": "Вентиляция",
    "climate": "Климат",
    "cover": "Шторы / жалюзи",
    "scene": "Сцены",
    "script": "Скрипты",
    "sensor": "Сенсоры",
    "binary_sensor": "Датчики",
    "media_player": "Медиа",
    "humidifier": "Увлажнители",
    "vacuum": "Пылесосы",
    "lock": "Замки",
    "input_boolean": "Тумблеры",
    "automation": "Автоматизации",
    "person": "Люди",
    "camera": "Камеры",
}

# Уточнения по device_class — для будущих версий
SWITCH_BY_DEVICE_CLASS = {
    "outlet": ("🔌", "Розетка"),
    "switch": ("🎚", "Свич"),
}

SENSOR_BY_DEVICE_CLASS = {
    "temperature": ("🌡", "Температура"),
    "humidity": ("💧", "Влажность"),
    "pressure": ("🌬", "Давление"),
    "co2": ("🟢", "CO₂"),
    "pm25": ("🌫", "PM2.5"),
    "pm10": ("🌫", "PM10"),
    "battery": ("🔋", "Батарея"),
    "illuminance": ("🔆", "Освещённость"),
    "energy": ("⚡", "Энергия"),
    "power": ("⚡", "Мощность"),
    "voltage": ("🔌", "Напряжение"),
    "current": ("⚡", "Ток"),
    "signal_strength": ("📶", "Сигнал"),
}

BINARY_BY_DEVICE_CLASS = {
    "motion": ("🚶", "Движение"),
    "occupancy": ("🚶", "Присутствие"),
    "presence": ("🚶", "Присутствие"),
    "door": ("🚪", "Дверь"),
    "window": ("🪟", "Окно"),
    "moisture": ("💧", "Протечка"),
    "smoke": ("🔥", "Дым"),
    "gas": ("☢", "Газ"),
    "battery": ("🔋", "Батарея"),
    "lock": ("🔐", "Замок"),
    "problem": ("⚠", "Проблема"),
    "running": ("🟢", "Работает"),
    "tamper": ("🛡", "Вскрытие"),
}

# Binary state labels: device_class → (on_icon, on_text, off_icon, off_text)
# Семантика выбрана так, чтобы аларм-состояние всегда выделялось
# тревожной иконкой, нормальное — нейтральной/✅.
BINARY_STATE = {
    "motion":       ("🚶", "Движение",  "⚪", "—"),
    "occupancy":    ("🚶", "Есть",      "⚪", "Нет"),
    "presence":     ("🚶", "Есть",      "⚪", "Нет"),
    "door":         ("🟧", "Открыта",   "🟩", "Закрыта"),
    "window":       ("🟧", "Открыто",   "🟩", "Закрыто"),
    "garage_door":  ("🟧", "Открыта",   "🟩", "Закрыта"),
    "opening":      ("🟧", "Открыто",   "🟩", "Закрыто"),
    "moisture":     ("💧", "Протечка!", "✅", "Сухо"),
    "smoke":        ("🔥", "ДЫМ!",      "✅", "Чисто"),
    "gas":          ("☢",  "ГАЗ!",      "✅", "Чисто"),
    "co":           ("☢",  "CO!",       "✅", "Чисто"),
    "carbon_monoxide": ("☢", "CO!",     "✅", "Чисто"),
    "battery":      ("🔴", "Низкий",    "🔋", "ОК"),
    "lock":         ("🔓", "Открыт",    "🔐", "Заперт"),
    "problem":      ("⚠",  "Проблема",  "✅", "ОК"),
    "safety":       ("⚠",  "Опасно",    "✅", "Безопасно"),
    "running":      ("🟢", "Работает",  "⚪", "Стоит"),
    "tamper":       ("🛡", "Вскрытие!", "✅", "ОК"),
    "connectivity": ("🟢", "Online",    "🔴", "Offline"),
    "power":        ("⚡", "Под током", "⚪", "Без тока"),
    "plug":         ("🔌", "Подключён", "⚪", "Отключён"),
    "heat":         ("🔥", "Горячо",    "❄", "Прохладно"),
    "cold":         ("❄",  "Холодно",   "✅", "Норма"),
    "vibration":    ("📳", "Вибрация",  "⚪", "Тихо"),
    "sound":        ("🔊", "Звук",      "🔇", "Тихо"),
    "light":        ("☀",  "Светло",    "🌑", "Темно"),
    "update":       ("🔔", "Доступно",  "✅", "Актуально"),
    "moving":       ("🚚", "В движении","⚪", "На месте"),
}


def binary_state_label(entity_id: str, attributes: dict, state: str) -> tuple[str, str]:
    """Возвращает (icon, label) для binary_sensor по device_class и текущему state."""
    dc = attributes.get("device_class") or ""
    on_icon, on_label, off_icon, off_label = BINARY_STATE.get(dc, ("🟢", "Активно", "⚪", "—"))
    if state == "on":
        return on_icon, on_label
    if state == "off":
        return off_icon, off_label
    # unavailable / unknown
    return "❓", str(state)


def format_state(state_val, attributes: dict | None = None) -> str:
    """Округлить числовой state до разумного числа знаков.

    HA REST API возвращает raw state без округления. Если в атрибутах задан
    suggested_display_precision — используем его, иначе — максимум 2 знака.
    Не-числовые значения возвращаем как строку без изменений.
    """
    s = str(state_val)
    # Быстрый отфильтр: должны быть только цифры/точка/минус
    try:
        f = float(s)
    except (ValueError, TypeError):
        return s
    # Целые — без хвоста ".0"
    if f == int(f) and "." not in s and "e" not in s.lower():
        return s
    precision = 2
    if attributes:
        sp = attributes.get("suggested_display_precision")
        if isinstance(sp, int) and 0 <= sp <= 6:
            precision = sp
    formatted = f"{f:.{precision}f}"
    # Trim trailing zeros: "12.30" → "12.3", "12.00" → "12"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted

# Critical entities — требуют confirmation перед действием
CRITICAL_KEYWORDS = (
    "lock", "alarm", "water", "boiler", "gas", "fire",
    "smoke_alarm", "siren",
)


def is_critical(entity_id: str) -> bool:
    eid = entity_id.lower()
    return any(k in eid for k in CRITICAL_KEYWORDS)


def icon_for(entity_id: str, attributes: dict) -> str:
    domain = entity_id.split(".", 1)[0]
    dc = attributes.get("device_class")

    if domain == "switch" and dc in SWITCH_BY_DEVICE_CLASS:
        return SWITCH_BY_DEVICE_CLASS[dc][0]
    if domain == "sensor" and dc in SENSOR_BY_DEVICE_CLASS:
        return SENSOR_BY_DEVICE_CLASS[dc][0]
    if domain == "binary_sensor" and dc in BINARY_BY_DEVICE_CLASS:
        return BINARY_BY_DEVICE_CLASS[dc][0]
    return DOMAIN_ICON.get(domain, "•")


def label_for_domain(domain: str) -> str:
    return DOMAIN_LABEL.get(domain, domain.title())


# Какие домены показываем в "По типам"
ACTIONABLE_DOMAINS = (
    "light", "switch", "fan", "climate", "cover",
    "scene", "script", "media_player", "humidifier",
    "lock", "input_boolean", "camera",
)

VIEWABLE_DOMAINS = (
    "sensor", "binary_sensor", "person",
)


def domain_is_actionable(domain: str) -> bool:
    return domain in ACTIONABLE_DOMAINS


# Иконки для комнат — по подстроке в имени area (case-insensitive).
# Порядок имеет значение: "прихожая" должна сматчиться раньше "коридор" если оба в имени.
ROOM_ICONS: tuple[tuple[str, str], ...] = (
    ("кухн",      "🍳"),
    ("kitchen",   "🍳"),
    ("гостин",    "🛋"),
    ("livingroom","🛋"),
    ("ванн",      "🛁"),
    ("bath",      "🛁"),
    ("туалет",    "🚽"),
    ("сортир",    "🚽"),
    ("toilet",    "🚽"),
    ("wc",        "🚽"),
    ("спальн",    "🛏"),
    ("bedroom",   "🛏"),
    ("детск",     "🧸"),
    ("kids",      "🧸"),
    ("nursery",   "🧸"),
    ("прихож",    "🧥"),
    ("hallway",   "🧥"),
    ("корид",     "🚪"),
    ("hall",      "🚪"),
    ("балкон",    "🌿"),
    ("лоджия",    "🌿"),
    ("balcony",   "🌿"),
    ("сервер",    "🖥"),
    ("server",    "🖥"),
    ("дача",      "🏡"),
    ("dacha",     "🏡"),
    ("гараж",     "🚗"),
    ("garage",    "🚗"),
    ("кабинет",   "💼"),
    ("office",    "💼"),
    ("workshop",  "🛠"),
    ("мастерск",  "🛠"),
    ("прачеч",    "🧺"),
    ("laundry",   "🧺"),
    ("кладов",    "📦"),
    ("storage",   "📦"),
    ("сад",       "🌳"),
    ("garden",    "🌳"),
    ("yard",      "🌳"),
    ("улиц",      "🌳"),
    ("street",    "🌳"),
)


def icon_for_area(name: str) -> str:
    """Подобрать иконку для комнаты по её имени."""
    n = (name or "").lower()
    for key, icon in ROOM_ICONS:
        if key in n:
            return icon
    return "🏠"
