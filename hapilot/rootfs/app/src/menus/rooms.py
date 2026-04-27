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
    DOMAIN_LABEL,
    binary_state_label,
    domain_is_actionable,
    icon_for,
    icon_for_area,
    label_for_domain,
)
from ..ha_client import HASnapshot
from ..visibility import VisibilityStore

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
    rows.append([InlineKeyboardButton(text="← По комнатам", callback_data="r:")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_room_domain(
    snap: HASnapshot,
    area_id: str,
    domain: str,
    id_cache: dict,
    vis: VisibilityStore,
    edit: bool = False,
) -> InlineKeyboardMarkup:
    """Конкретный домен в комнате — список entity с кнопками."""
    entities = [
        e for e in snap.entities_in_area(area_id)
        if e["entity_id"].startswith(domain + ".")
        and not e.get("disabled_by")
        and (edit or vis.is_visible(e))
    ]
    entities.sort(key=lambda e: (e.get("name") or e.get("original_name") or e["entity_id"]).lower())

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
            value_str = f"{state_val}{unit}" if unit else state_val
            text = f"{icon} {fname[:25]}: {value_str}"[:60]
            btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))

    rows = _chunk(btns)  # авто-grid по длине
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"r:{area_id}")])
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
