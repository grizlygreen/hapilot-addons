"""Меню «По комнатам»."""

from collections import defaultdict
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _chunk(
    buttons: Iterable[InlineKeyboardButton], per_row: int | None = None
) -> list[list[InlineKeyboardButton]]:
    """Разбить кнопки на ряды.

    Если per_row не задан — авто-выбор по длине самой длинной кнопки в группе:
      max_len ≤ 10 → 3 в ряд
      max_len ≤ 18 → 2 в ряд
      иначе         → 1 в ряд

    Для смешанных длин (короткие + длинные) каждая длинная (>18) идёт на свою строку,
    короткие идут партиями.
    """
    btns = list(buttons)
    if not btns:
        return []

    if per_row is not None:
        target = per_row
    else:
        max_len = max(len(b.text) for b in btns)
        if max_len <= 10:
            target = 3
        elif max_len <= 18:
            target = 2
        else:
            target = 1

    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for b in btns:
        # Длинная кнопка → завершаем текущий ряд и кладём отдельно
        if per_row is None and len(b.text) > 18 and target > 1:
            if current:
                rows.append(current)
                current = []
            rows.append([b])
            continue
        current.append(b)
        if len(current) >= target:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return rows

from ..classifiers import (
    BINARY_BY_DEVICE_CLASS,
    DOMAIN_LABEL,
    SENSOR_BY_DEVICE_CLASS,
    binary_state_label,
    domain_is_actionable,
    format_state,
    icon_for,
    icon_for_area,
    label_for_domain,
)
from ..ha_client import HASnapshot
from ..visibility import VisibilityStore

# Если в одной комнате больше этого числа sensor/binary_sensor — разбиваем по device_class
ROOM_SPLIT_THRESHOLD = 10
ROOM_SPLIT_DOMAINS = ("sensor", "binary_sensor")

# Сколько entity показываем на одной странице меню (TG лимит ~100 кнопок + reply_markup size)
PAGE_SIZE = 60

# CallBack data format (≤64 bytes):
#   r:                          → меню "По комнатам" (список областей)
#   r:<area_id>                 → область → группы по доменам
#   r:<area_id>:<domain>        → область + домен → entities
#   e:<short_id>                → действие над entity (lookup в кэше)


def kb_rooms_root(snap: HASnapshot, vis: VisibilityStore, edit: bool = False) -> InlineKeyboardMarkup:
    """Список областей."""
    rows = []
    # считаем сколько entities в каждой area
    by_area: dict[str, int] = defaultdict(int)
    device_areas = {d["id"]: d.get("area_id") for d in snap.devices}
    for e in snap.entities:
        if e.get("disabled_by"):
            continue
        if not edit and not vis.is_visible(e):
            continue
        ea = e.get("area_id") or device_areas.get(e.get("device_id"))
        if ea:
            by_area[ea] += 1

    sorted_areas = sorted(snap.areas, key=lambda a: a["name"].lower())
    btns = []
    for area in sorted_areas:
        n = by_area.get(area["area_id"], 0)
        if n == 0:
            continue
        ai = icon_for_area(area["name"])
        btns.append(InlineKeyboardButton(
            text=f"{ai} {area['name']} ({n})",
            callback_data=f"r:{area['area_id']}"[:64],
        ))
    rows.extend(_chunk(btns))
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="m")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_room_domains(
    snap: HASnapshot, area_id: str, vis: VisibilityStore, edit: bool = False
) -> InlineKeyboardMarkup:
    """Внутри области — список доменов."""
    entities = snap.entities_in_area(area_id)
    by_domain: dict[str, int] = defaultdict(int)
    for e in entities:
        if e.get("disabled_by"):
            continue
        if not edit and not vis.is_visible(e):
            continue
        domain = e["entity_id"].split(".", 1)[0]
        by_domain[domain] += 1

    rows = []
    domain_order = (
        "light", "switch", "fan", "climate", "humidifier", "cover",
        "media_player", "lock", "camera", "scene", "script",
        "sensor", "binary_sensor",
    )
    btns = []
    for d in domain_order:
        if d not in by_domain:
            continue
        btns.append(InlineKeyboardButton(
            text=f"{icon_for(f'{d}.x', {})} {label_for_domain(d)} ({by_domain[d]})",
            callback_data=f"r:{area_id}:{d}"[:64],
        ))
    rows.extend(_chunk(btns))
    rows.append([
        InlineKeyboardButton(text="← По комнатам", callback_data="r:"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_room_classes(
    snap: HASnapshot, area_id: str, domain: str, vis: VisibilityStore, edit: bool = False,
) -> InlineKeyboardMarkup:
    """Подменю device_class для sensor/binary_sensor внутри комнаты (когда entity > 10)."""
    by_class: dict[str, int] = defaultdict(int)
    for e in snap.entities_in_area(area_id):
        if not e["entity_id"].startswith(domain + "."):
            continue
        if e.get("disabled_by"):
            continue
        if not edit and not vis.is_visible(e):
            continue
        st = snap.state(e["entity_id"]) or {}
        dc = st.get("attributes", {}).get("device_class") or "_other"
        by_class[dc] += 1

    table = SENSOR_BY_DEVICE_CLASS if domain == "sensor" else BINARY_BY_DEVICE_CLASS
    known = [c for c in table if c in by_class]
    unknown = sorted(c for c in by_class if c not in table and c != "_other")
    order = known + unknown
    if "_other" in by_class:
        order.append("_other")

    btns = []
    for dc in order:
        n = by_class[dc]
        if dc == "_other":
            text = f"❓ Прочее ({n})"
        elif dc in table:
            icon, label = table[dc]
            text = f"{icon} {label} ({n})"
        else:
            text = f"• {dc} ({n})"
        btns.append(InlineKeyboardButton(
            text=text, callback_data=f"r:{area_id}:{domain}:{dc}"[:64],
        ))
    rows = _chunk(btns)
    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data=f"r:{area_id}"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_room_domain(
    snap: HASnapshot,
    area_id: str,
    domain: str,
    id_cache: dict,
    vis: VisibilityStore,
    edit: bool = False,
    device_class: str | None = None,
    page: int = 1,
) -> InlineKeyboardMarkup:
    """Конкретный домен в комнате — список entity с кнопками.

    Если device_class задан — фильтр по нему ("_other" = без device_class).
    Пагинация: PAGE_SIZE entity на страницу. Если total > PAGE_SIZE — кнопки ◀ N/M ▶.
    """
    entities = [
        e for e in snap.entities_in_area(area_id)
        if e["entity_id"].startswith(domain + ".")
        and not e.get("disabled_by")
        and (edit or vis.is_visible(e))
    ]
    if device_class is not None:
        def _dc_match(e: dict) -> bool:
            st = snap.state(e["entity_id"]) or {}
            dc = st.get("attributes", {}).get("device_class") or ""
            return (device_class == "_other" and not dc) or (dc == device_class)
        entities = [e for e in entities if _dc_match(e)]
    entities.sort(key=lambda e: (e.get("name") or e.get("original_name") or e["entity_id"]).lower())

    # Пагинация
    all_entities = entities
    total_pages = max(1, (len(all_entities) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    entities = all_entities[start:start + PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []

    # Массовые действия для actionable доменов (по ВСЕМ entity домена, не только странице)
    if not edit and domain in ("light", "switch", "fan") and len(all_entities) >= 2:
        on_count = sum(
            1 for e in all_entities
            if (snap.state(e["entity_id"]) or {}).get("state") == "on"
        )
        off_count = len(all_entities) - on_count
        rows.append([
            InlineKeyboardButton(
                text=f"🟢 Все вкл ({off_count})",
                callback_data=f"ba:{area_id}:{domain}:on"[:64],
            ),
            InlineKeyboardButton(
                text=f"⚪ Все выкл ({on_count})",
                callback_data=f"ba:{area_id}:{domain}:off"[:64],
            ),
        ])

    btns = []
    for e in entities:
        eid = e["entity_id"]
        st = snap.state(eid) or {}
        attrs = st.get("attributes", {})
        fname = attrs.get("friendly_name") or eid
        state_val = st.get("state", "?")
        icon = icon_for(eid, attrs)

        # Сокращаем + кэшируем под коротким id
        short = _short_id(eid, id_cache)
        # Запомним «родителя» этой entity — для smart back
        id_cache.setdefault("_parent", {})[short] = f"r:{area_id}:{domain}"

        if edit:
            visible_now = vis.is_visible(e)
            vis_mark = "✅" if visible_now else "🙈"
            text = f"{vis_mark} {icon} {fname[:30]}"
            btns.append(InlineKeyboardButton(text=text, callback_data=f"v:{short}"[:64]))
        elif domain_is_actionable(domain):
            mark = "🟢" if state_val in ("on", "playing", "open", "unlocked", "home") else "⚪"
            text = f"{mark} {icon} {fname[:30]}"
            btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))
        elif eid.startswith("binary_sensor."):
            bs_icon, bs_label = binary_state_label(eid, attrs, state_val)
            text = f"{bs_icon} {fname[:22]}: {bs_label}"[:60]
            btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))
        else:
            unit = attrs.get("unit_of_measurement", "")
            val = format_state(state_val, attrs)
            value_str = f"{val}{unit}" if unit else val
            text = f"{icon} {fname[:25]}: {value_str}"[:60]
            btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))

    rows.extend(_chunk(btns))  # авто-grid по длине

    # Пагинация
    if total_pages > 1:
        dc_token = device_class if device_class is not None else "_all"
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(
                text="◀", callback_data=f"r:{area_id}:{domain}:{dc_token}:p{page-1}"[:64]))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(InlineKeyboardButton(
                text="▶", callback_data=f"r:{area_id}:{domain}:{dc_token}:p{page+1}"[:64]))
        rows.append(nav)

    # Smart-back: если был фильтр по классу — назад в подменю классов
    if device_class is not None and domain in ROOM_SPLIT_DOMAINS:
        back_cb = f"r:{area_id}:{domain}"
        back_label = "← Классы"
    else:
        back_cb = f"r:{area_id}"
        back_label = "← Назад"
    rows.append([
        InlineKeyboardButton(text=back_label, callback_data=back_cb),
        InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_id(entity_id: str, cache: dict) -> str:
    """Сократить entity_id до короткого hash для callback_data (≤ 64 байт)."""
    rev = cache.setdefault("_rev", {})
    if entity_id in rev:
        return rev[entity_id]
    short = f"{len(rev):x}"
    rev[entity_id] = short
    cache[short] = entity_id
    return short
