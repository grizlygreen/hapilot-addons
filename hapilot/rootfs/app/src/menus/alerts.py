"""Меню «Алерты» — активные тревоги, уведомления HA, недоступные устройства."""

from datetime import datetime, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..classifiers import binary_state_label
from ..ha_client import HASnapshot

# Какие device_class бинарных датчиков считать «тревожными»
ALERT_CLASSES = {
    "smoke", "gas", "co", "carbon_monoxide", "moisture",
    "problem", "safety", "tamper",
}

# Критичные домены чьи unavailable бьют по работе дома
CRITICAL_DOMAINS = (
    "light", "switch", "climate", "cover", "lock",
    "fan", "humidifier", "media_player", "vacuum",
)


def _alert_count(snap: HASnapshot) -> int:
    n = 0
    for s in snap.states.values():
        eid = s.get("entity_id", "")
        if not eid.startswith("binary_sensor."):
            continue
        attrs = s.get("attributes", {})
        if attrs.get("device_class") not in ALERT_CLASSES:
            continue
        if s.get("state") == "on":
            n += 1
    return n


def _unavail_count(snap: HASnapshot) -> int:
    n = 0
    for s in snap.states.values():
        eid = s.get("entity_id", "")
        domain = eid.split(".", 1)[0]
        if domain not in CRITICAL_DOMAINS:
            continue
        if s.get("state") in ("unavailable", "unknown"):
            n += 1
    return n


def kb_alerts_root(snap: HASnapshot, notif_count: int) -> InlineKeyboardMarkup:
    """Главное меню Алертов."""
    alerts = _alert_count(snap)
    unavail = _unavail_count(snap)
    rows = [
        [InlineKeyboardButton(
            text=f"🔥 Активные тревоги ({alerts})", callback_data="al:problems")],
        [InlineKeyboardButton(
            text=f"📋 Уведомления HA ({notif_count})", callback_data="al:notif")],
        [InlineKeyboardButton(
            text=f"⚠ Недоступны устройства ({unavail})", callback_data="al:unavail")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="m")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_alerts_problems(snap: HASnapshot) -> tuple[str, InlineKeyboardMarkup]:
    """Активные тревожные binary_sensor + быстрые действия."""
    items = []
    classes_active: set[str] = set()
    for s in snap.states.values():
        eid = s.get("entity_id", "")
        if not eid.startswith("binary_sensor."):
            continue
        attrs = s.get("attributes", {})
        dc = attrs.get("device_class")
        if dc not in ALERT_CLASSES:
            continue
        if s.get("state") == "on":
            fn = attrs.get("friendly_name") or eid
            icon, label = binary_state_label(eid, attrs, "on")
            since = s.get("last_changed", "") or s.get("last_updated", "")
            items.append((eid, fn, icon, label, since))
            classes_active.add(dc)

    items.sort(key=lambda x: x[4], reverse=True)

    if not items:
        text = "🔥 <b>Активные тревоги</b>\n\nНи одной активной тревоги. ✅"
    else:
        lines = [f"🔥 <b>Активные тревоги ({len(items)})</b>", ""]
        for eid, fn, icon, label, since in items[:25]:
            ago = _humanize_ago(since)
            lines.append(f"{icon} <b>{_h(fn)}</b>\n   <i>{label}</i> — {ago}")
        text = "\n".join(lines)

    # Состояние воды для контекстных кнопок
    water_on = snap.states.get("switch.water", {}).get("state") == "on"

    rows: list[list[InlineKeyboardButton]] = []
    # Smoke / gas / co — кнопка заглушить голос
    if classes_active & {"smoke", "gas", "co", "carbon_monoxide"}:
        rows.append([InlineKeyboardButton(
            text="🔕 Квитировать дым (5 мин тишины)",
            callback_data="al:ack_smoke")])
    # Moisture — кнопки управления водой
    if "moisture" in classes_active:
        if water_on:
            rows.append([InlineKeyboardButton(
                text="🚫 Перекрыть воду", callback_data="al:water_off")])
        else:
            rows.append([InlineKeyboardButton(
                text="💧 Открыть воду", callback_data="al:water_on")])
        rows.append([InlineKeyboardButton(
            text="🔕 Квитировать протечку (5 мин)",
            callback_data="al:ack_leak")])
    # Tamper / safety / problem — без авто-действий, только индикация
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="al:problems")])
    rows.append([InlineKeyboardButton(text="← Алерты", callback_data="al:")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def kb_alerts_unavail(snap: HASnapshot) -> tuple[str, InlineKeyboardMarkup]:
    """Недоступные критичные устройства."""
    items = []
    for s in snap.states.values():
        eid = s.get("entity_id", "")
        domain = eid.split(".", 1)[0]
        if domain not in CRITICAL_DOMAINS:
            continue
        if s.get("state") not in ("unavailable", "unknown"):
            continue
        attrs = s.get("attributes", {})
        fn = attrs.get("friendly_name") or eid
        since = s.get("last_changed", "") or s.get("last_updated", "")
        items.append((eid, fn, s.get("state"), since))

    items.sort(key=lambda x: x[3])

    if not items:
        text = "⚠ <b>Недоступные устройства</b>\n\nВсе критичные устройства онлайн. ✅"
    else:
        lines = [f"⚠ <b>Недоступны ({len(items)})</b>", ""]
        for eid, fn, state, since in items[:30]:
            ago = _humanize_ago(since)
            lines.append(f"• <code>{_h(eid)}</code>\n   <i>{state}</i> — {ago}")
        text = "\n".join(lines)

    rows = [[InlineKeyboardButton(text="← Алерты", callback_data="al:")]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _h(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _humanize_ago(iso_ts: str) -> str:
    if not iso_ts:
        return "?"
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s} сек назад"
        if s < 3600:
            return f"{s // 60} мин назад"
        if s < 86400:
            return f"{s // 3600} ч {(s % 3600) // 60} мин назад"
        return f"{s // 86400} дн назад"
    except Exception:
        return "?"
