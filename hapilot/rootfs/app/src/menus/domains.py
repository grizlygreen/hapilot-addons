"""Меню «По типам» — список доменов → entities, отсортированных по area."""

from collections import defaultdict
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..classifiers import (
    binary_state_label,
    domain_is_actionable,
    icon_for,
    icon_for_area,
    label_for_domain,
)
from ..ha_client import HASnapshot
from ..visibility import VisibilityStore


# Какие домены показывать в меню (порядок)
DOMAIN_ORDER = (
    "light", "switch", "fan", "climate", "humidifier", "cover",
    "media_player", "lock", "camera", "scene", "script",
    "sensor", "binary_sensor",
)


def _chunk(buttons: Iterable[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Авто-grid: 3 кнопки в ряд если ≤10 симв, 2 если ≤18, иначе 1."""
    btns = list(buttons)
    if not btns:
        return []
    max_len = max(len(b.text) for b in btns)
    target = 3 if max_len <= 10 else (2 if max_len <= 18 else 1)
    rows: list[list[InlineKeyboardButton]] = []
    cur: list[InlineKeyboardButton] = []
    for b in btns:
        if len(b.text) > 18 and target > 1:
            if cur:
                rows.append(cur); cur = []
            rows.append([b])
            continue
        cur.append(b)
        if len(cur) >= target:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    return rows


def kb_domains_root(snap: HASnapshot, vis: VisibilityStore) -> InlineKeyboardMarkup:
    """Главное меню «По типам»."""
    by_domain: dict[str, int] = defaultdict(int)
    for e in snap.entities:
        if e.get("disabled_by"):
            continue
        if not vis.is_visible(e):
            continue
        domain = e["entity_id"].split(".", 1)[0]
        if domain in DOMAIN_ORDER:
            by_domain[domain] += 1

    btns = []
    for d in DOMAIN_ORDER:
        n = by_domain.get(d, 0)
        if n == 0:
            continue
        btns.append(InlineKeyboardButton(
            text=f"{icon_for(f'{d}.x', {})} {label_for_domain(d)} ({n})",
            callback_data=f"d:{d}"[:64],
        ))
    rows = _chunk(btns)
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="m")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_domain_entities(
    snap: HASnapshot, domain: str, id_cache: dict, vis: VisibilityStore,
) -> tuple[str, InlineKeyboardMarkup]:
    """Список entities одного домена, сгруппированный по комнатам."""
    device_areas = {d["id"]: d.get("area_id") for d in snap.devices}
    areas_by_id = {a["area_id"]: a["name"] for a in snap.areas}

    # Группируем по area
    by_area: dict[str, list[dict]] = defaultdict(list)
    for e in snap.entities:
        if not e["entity_id"].startswith(domain + "."):
            continue
        if e.get("disabled_by"):
            continue
        if not vis.is_visible(e):
            continue
        area_id = e.get("area_id") or device_areas.get(e.get("device_id")) or "_no_area"
        by_area[area_id].append(e)

    rows = []
    # Сначала области по алфавиту, потом "_no_area"
    sorted_areas = sorted(
        [a for a in by_area if a != "_no_area"],
        key=lambda a: areas_by_id.get(a, a).lower(),
    )
    if "_no_area" in by_area:
        sorted_areas.append("_no_area")

    for area_id in sorted_areas:
        ents = by_area[area_id]
        ents.sort(key=lambda e: (
            (e.get("name") or "")
            or e.get("original_name") or e["entity_id"]
        ).lower())
        # Заголовок группы — пустая кнопка-разделитель (TG не любит без callback)
        if area_id == "_no_area":
            header = "🏠 Без комнаты"
        else:
            ai = icon_for_area(areas_by_id.get(area_id, area_id))
            header = f"{ai} {areas_by_id.get(area_id, area_id)}"
        rows.append([InlineKeyboardButton(text=f"— {header} —", callback_data="noop")])

        # Entities
        btns = []
        for e in ents:
            eid = e["entity_id"]
            st = snap.state(eid) or {}
            attrs = st.get("attributes", {})
            fname = attrs.get("friendly_name") or eid
            state_val = st.get("state", "?")
            short = _short_id(eid, id_cache)
            # parent для smart back
            id_cache.setdefault("_parent", {})[short] = f"d:{domain}"

            icon = icon_for(eid, attrs)
            if eid.startswith("binary_sensor."):
                bs_icon, bs_label = binary_state_label(eid, attrs, state_val)
                text = f"{bs_icon} {fname[:22]}: {bs_label}"
            elif domain_is_actionable(domain):
                mark = "🟢" if state_val in ("on", "playing", "open", "unlocked", "home") else "⚪"
                text = f"{mark} {icon} {fname[:30]}"
            else:
                unit = attrs.get("unit_of_measurement", "")
                value_str = f"{state_val}{unit}" if unit else state_val
                text = f"{icon} {fname[:25]}: {value_str}"[:60]
            btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))
        rows.extend(_chunk(btns))

    rows.append([InlineKeyboardButton(text="← По типам", callback_data="d:")])
    title = f"{icon_for(f'{domain}.x',{})} <b>{label_for_domain(domain)}</b>"
    return title, InlineKeyboardMarkup(inline_keyboard=rows)


def _short_id(entity_id: str, cache: dict) -> str:
    rev = cache.setdefault("_rev", {})
    if entity_id in rev:
        return rev[entity_id]
    short = f"{len(rev):x}"
    rev[entity_id] = short
    cache[short] = entity_id
    return short
