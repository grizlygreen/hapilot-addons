"""Меню «Избранное» — закреплённые entity для быстрого доступа."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..classifiers import binary_state_label, domain_is_actionable, format_state, icon_for
from ..favorites import FavoritesStore
from ..ha_client import HASnapshot
from .rooms import _chunk, _short_id


def kb_favorites(
    snap: HASnapshot, favs: FavoritesStore, id_cache: dict
) -> InlineKeyboardMarkup:
    """Список избранных entity (в порядке добавления)."""
    rows: list[list[InlineKeyboardButton]] = []
    btns: list[InlineKeyboardButton] = []

    for eid in favs.items:
        # entity может исчезнуть из HA — пропускаем
        ent = next((e for e in snap.entities if e["entity_id"] == eid), None)
        if not ent:
            continue
        if ent.get("disabled_by"):
            continue

        st = snap.state(eid) or {}
        attrs = st.get("attributes", {})
        fname = attrs.get("friendly_name") or eid
        state_val = st.get("state", "?")
        domain = eid.split(".", 1)[0]
        icon = icon_for(eid, attrs)

        short = _short_id(eid, id_cache)
        # smart back — возвращаемся в избранное
        id_cache.setdefault("_parent", {})[short] = "f:"

        if eid.startswith("binary_sensor."):
            bs_icon, bs_label = binary_state_label(eid, attrs, state_val)
            text = f"{bs_icon} {fname[:22]}: {bs_label}"[:60]
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
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="m")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
