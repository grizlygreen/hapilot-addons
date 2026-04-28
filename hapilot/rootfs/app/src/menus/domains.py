"""Меню «По типам» — список доменов → entities, отсортированных по area."""

from collections import defaultdict
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..classifiers import (
    BINARY_BY_DEVICE_CLASS,
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

# Домены, которые ВСЕГДА разбиваем по device_class (много entity → не влезет в TG)
SPLIT_BY_CLASS = ("sensor", "binary_sensor")

# Сколько entity на одной странице меню
PAGE_SIZE = 60


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


def kb_domain_classes(
    snap: HASnapshot, domain: str, vis: VisibilityStore,
) -> tuple[str, InlineKeyboardMarkup]:
    """Подменю device_class для sensor/binary_sensor."""
    by_class: dict[str, int] = defaultdict(int)
    for e in snap.entities:
        if not e["entity_id"].startswith(domain + "."):
            continue
        if e.get("disabled_by"):
            continue
        if not vis.is_visible(e):
            continue
        st = snap.state(e["entity_id"]) or {}
        dc = st.get("attributes", {}).get("device_class") or "_other"
        by_class[dc] += 1

    table = SENSOR_BY_DEVICE_CLASS if domain == "sensor" else BINARY_BY_DEVICE_CLASS
    # Сортировка: known classes по таблице, остальные — алфавит, _other в конце
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
            text=text, callback_data=f"d:{domain}:{dc}"[:64],
        ))
    rows = _chunk(btns)
    rows.append([
        InlineKeyboardButton(text="← По типам", callback_data="d:"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
    ])
    title = f"{icon_for(f'{domain}.x',{})} <b>{label_for_domain(domain)}</b> — выбери класс"
    return title, InlineKeyboardMarkup(inline_keyboard=rows)


def kb_domain_entities(
    snap: HASnapshot, domain: str, id_cache: dict, vis: VisibilityStore,
    device_class: str | None = None,
    page: int = 1,
) -> tuple[str, InlineKeyboardMarkup]:
    """Список entities одного домена, сгруппированный по комнатам.

    Если device_class задан — фильтруем по нему (для sensor/binary_sensor).
    "_other" = entities без device_class.
    Пагинация: PAGE_SIZE entity на страницу. Группируем сначала по area, потом
    разбиваем на страницы (заголовки area попадают в свою страницу).
    """
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
        if device_class is not None:
            st = snap.state(e["entity_id"]) or {}
            dc = st.get("attributes", {}).get("device_class") or ""
            if device_class == "_other":
                if dc:
                    continue
            else:
                if dc != device_class:
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

    # Считаем total для решения — нужна ли пагинация
    total = sum(len(by_area[a]) for a in sorted_areas)
    paginate = total > PAGE_SIZE
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if paginate else 1
    page = max(1, min(page, total_pages))

    if not paginate:
        # Старый формат: заголовки area + entity, всё на одной странице
        for area_id in sorted_areas:
            ents = by_area[area_id]
            ents.sort(key=lambda e: (
                (e.get("name") or "")
                or e.get("original_name") or e["entity_id"]
            ).lower())
            if area_id == "_no_area":
                header = "🏠 Без комнаты"
            else:
                ai = icon_for_area(areas_by_id.get(area_id, area_id))
                header = f"{ai} {areas_by_id.get(area_id, area_id)}"
            rows.append([InlineKeyboardButton(text=f"— {header} —", callback_data="noop")])

            btns = []
            for e in ents:
                eid = e["entity_id"]
                st = snap.state(eid) or {}
                attrs = st.get("attributes", {})
                fname = attrs.get("friendly_name") or eid
                state_val = st.get("state", "?")
                short = _short_id(eid, id_cache)
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
                    val = format_state(state_val, attrs)
                    value_str = f"{val}{unit}" if unit else val
                    text = f"{icon} {fname[:25]}: {value_str}"[:60]
                btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))
            rows.extend(_chunk(btns))
    else:
        # Пагинированный flat-список: имя area дописываем в текст entity
        flat: list[tuple[dict, str]] = []  # (entity, area_label)
        for area_id in sorted_areas:
            ents = by_area[area_id]
            ents.sort(key=lambda e: (
                (e.get("name") or "")
                or e.get("original_name") or e["entity_id"]
            ).lower())
            area_label = "Без" if area_id == "_no_area" else areas_by_id.get(area_id, area_id)
            for e in ents:
                flat.append((e, area_label))

        start = (page - 1) * PAGE_SIZE
        page_items = flat[start:start + PAGE_SIZE]

        btns = []
        for e, area_label in page_items:
            eid = e["entity_id"]
            st = snap.state(eid) or {}
            attrs = st.get("attributes", {})
            fname = attrs.get("friendly_name") or eid
            state_val = st.get("state", "?")
            short = _short_id(eid, id_cache)
            dc_token = device_class if device_class is not None else "_all"
            id_cache.setdefault("_parent", {})[short] = f"d:{domain}:{dc_token}:p{page}"

            icon = icon_for(eid, attrs)
            # Префикс комнаты для контекста: «🍳 Кухня»
            area_short = area_label[:8]
            if eid.startswith("binary_sensor."):
                bs_icon, bs_label = binary_state_label(eid, attrs, state_val)
                text = f"{bs_icon} {area_short}/{fname[:18]}: {bs_label}"[:60]
            elif domain_is_actionable(domain):
                mark = "🟢" if state_val in ("on", "playing", "open", "unlocked", "home") else "⚪"
                text = f"{mark} {area_short}/{fname[:22]}"[:60]
            else:
                unit = attrs.get("unit_of_measurement", "")
                val = format_state(state_val, attrs)
                value_str = f"{val}{unit}" if unit else val
                text = f"{area_short}/{fname[:18]}: {value_str}"[:60]
            btns.append(InlineKeyboardButton(text=text, callback_data=f"e:{short}"[:64]))
        rows.extend(_chunk(btns))

        # Page navigation
        dc_token = device_class if device_class is not None else "_all"
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton(
                text="◀", callback_data=f"d:{domain}:{dc_token}:p{page-1}"[:64]))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav.append(InlineKeyboardButton(
                text="▶", callback_data=f"d:{domain}:{dc_token}:p{page+1}"[:64]))
        rows.append(nav)

    # Smart-back: для sensor/binary_sensor с device_class — обратно в подменю классов
    if device_class is not None and domain in SPLIT_BY_CLASS:
        back_cb = f"d:{domain}"
        back_label = "← Классы"
    else:
        back_cb = "d:"
        back_label = "← По типам"
    rows.append([
        InlineKeyboardButton(text=back_label, callback_data=back_cb),
        InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
    ])

    # Заголовок: + класс если задан
    title = f"{icon_for(f'{domain}.x',{})} <b>{label_for_domain(domain)}</b>"
    if device_class is not None:
        table = SENSOR_BY_DEVICE_CLASS if domain == "sensor" else BINARY_BY_DEVICE_CLASS
        if device_class == "_other":
            title += " / Прочее"
        elif device_class in table:
            title += f" / {table[device_class][1]}"
        else:
            title += f" / {device_class}"
    return title, InlineKeyboardMarkup(inline_keyboard=rows)


def _short_id(entity_id: str, cache: dict) -> str:
    rev = cache.setdefault("_rev", {})
    if entity_id in rev:
        return rev[entity_id]
    short = f"{len(rev):x}"
    rev[entity_id] = short
    cache[short] = entity_id
    return short
