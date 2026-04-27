"""Действия над entity."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..classifiers import is_critical
from ..ha_client import HAClient


def kb_entity_actions(
    entity_id: str, state: dict, short: str, back_to: str = "m"
) -> InlineKeyboardMarkup:
    """Клавиатура с действиями для конкретной entity."""
    domain = entity_id.split(".", 1)[0]
    state_val = state.get("state", "")
    attrs = state.get("attributes", {})
    rows = []

    if domain in ("light", "switch", "input_boolean", "fan", "humidifier"):
        if state_val == "on":
            rows.append([
                InlineKeyboardButton(text="⚪ Выключить", callback_data=f"a:{short}:off"),
            ])
        else:
            rows.append([
                InlineKeyboardButton(text="🟢 Включить", callback_data=f"a:{short}:on"),
            ])

    if domain == "light":
        color_modes = attrs.get("supported_color_modes") or []
        has_brightness = "brightness" in attrs or "brightness" in color_modes \
            or any(m in color_modes for m in ("color_temp", "rgb", "rgbw", "rgbww", "hs", "xy"))
        has_color_temp = "color_temp" in color_modes
        has_rgb = any(m in color_modes for m in ("rgb", "rgbw", "rgbww", "hs", "xy"))

        if has_brightness:
            rows.append([
                InlineKeyboardButton(text="-25%", callback_data=f"a:{short}:dim_down"),
                InlineKeyboardButton(text="+25%", callback_data=f"a:{short}:dim_up"),
            ])
        if has_color_temp:
            rows.append([
                InlineKeyboardButton(text="🔥 Тёплый", callback_data=f"a:{short}:temp:2700"),
                InlineKeyboardButton(text="☀ Дневной", callback_data=f"a:{short}:temp:4000"),
                InlineKeyboardButton(text="🌒 Холодный", callback_data=f"a:{short}:temp:6500"),
            ])
        if has_rgb:
            rows.append([
                InlineKeyboardButton(text="🔴", callback_data=f"a:{short}:rgb:255,0,0"),
                InlineKeyboardButton(text="🟠", callback_data=f"a:{short}:rgb:255,128,0"),
                InlineKeyboardButton(text="🟡", callback_data=f"a:{short}:rgb:255,230,0"),
                InlineKeyboardButton(text="🟢", callback_data=f"a:{short}:rgb:0,255,0"),
            ])
            rows.append([
                InlineKeyboardButton(text="🔵", callback_data=f"a:{short}:rgb:0,80,255"),
                InlineKeyboardButton(text="🟣", callback_data=f"a:{short}:rgb:160,0,255"),
                InlineKeyboardButton(text="🩷", callback_data=f"a:{short}:rgb:255,80,200"),
                InlineKeyboardButton(text="⚪", callback_data=f"a:{short}:rgb:255,255,255"),
            ])

    if domain == "fan":
        for preset in attrs.get("preset_modes", []) or []:
            rows.append([
                InlineKeyboardButton(
                    text=f"⚙ {preset}",
                    callback_data=f"a:{short}:preset:{preset}"[:64],
                )
            ])

    if domain == "scene":
        rows.append([
            InlineKeyboardButton(text="🎬 Активировать", callback_data=f"a:{short}:scene"),
        ])

    if domain == "script":
        rows.append([
            InlineKeyboardButton(text="▶ Запустить", callback_data=f"a:{short}:run"),
        ])

    if domain == "cover":
        rows.append([
            InlineKeyboardButton(text="↑ Открыть", callback_data=f"a:{short}:open"),
            InlineKeyboardButton(text="⏹", callback_data=f"a:{short}:stop"),
            InlineKeyboardButton(text="↓ Закрыть", callback_data=f"a:{short}:close"),
        ])

    if domain == "camera":
        rows.append([
            InlineKeyboardButton(text="📸 Снимок", callback_data=f"a:{short}:snapshot"),
        ])

    if domain == "lock":
        if state_val == "locked":
            rows.append([
                InlineKeyboardButton(
                    text="🔓 Отпереть (подтверждение)",
                    callback_data=f"c:{short}:unlock",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(text="🔐 Запереть", callback_data=f"a:{short}:lock"),
            ])

    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"e:{short}")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=back_to)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_confirm(entity_id: str, action: str, short: str) -> InlineKeyboardMarkup:
    """Confirmation dialog для критичных действий."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, выполнить", callback_data=f"a:{short}:{action}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"e:{short}")],
    ])


async def execute_action(
    ha: HAClient, entity_id: str, action: str
) -> tuple[bool, str]:
    """Выполнить действие. Возвращает (success, message)."""
    domain = entity_id.split(".", 1)[0]

    try:
        if action == "on":
            await ha.call_service(domain, "turn_on", entity_id)
        elif action == "off":
            await ha.call_service(domain, "turn_off", entity_id)
        elif action == "lock":
            await ha.call_service("lock", "lock", entity_id)
        elif action == "unlock":
            await ha.call_service("lock", "unlock", entity_id)
        elif action == "open":
            await ha.call_service("cover", "open_cover", entity_id)
        elif action == "close":
            await ha.call_service("cover", "close_cover", entity_id)
        elif action == "stop":
            await ha.call_service("cover", "stop_cover", entity_id)
        elif action == "scene":
            await ha.call_service("scene", "turn_on", entity_id)
        elif action == "run":
            await ha.call_service("script", "turn_on", entity_id)
        elif action == "dim_up":
            st = await ha.get_state(entity_id)
            cur = (st or {}).get("attributes", {}).get("brightness", 0) or 0
            new = min(255, cur + 64)
            await ha.call_service("light", "turn_on", entity_id, {"brightness": new})
        elif action == "dim_down":
            st = await ha.get_state(entity_id)
            cur = (st or {}).get("attributes", {}).get("brightness", 0) or 0
            new = max(0, cur - 64)
            if new == 0:
                await ha.call_service("light", "turn_off", entity_id)
            else:
                await ha.call_service("light", "turn_on", entity_id, {"brightness": new})
        elif action.startswith("preset:"):
            preset = action.split(":", 1)[1]
            await ha.call_service("fan", "set_preset_mode", entity_id, {"preset_mode": preset})
        elif action.startswith("temp:"):
            kelvin = int(action.split(":", 1)[1])
            await ha.call_service("light", "turn_on", entity_id,
                                  {"color_temp_kelvin": kelvin, "brightness_pct": 100})
        elif action.startswith("rgb:"):
            r, g, b = (int(x) for x in action.split(":", 1)[1].split(","))
            await ha.call_service("light", "turn_on", entity_id,
                                  {"rgb_color": [r, g, b], "brightness_pct": 100})
        else:
            return False, f"Неизвестное действие: {action}"
        return True, "Готово"
    except Exception as e:
        return False, f"Ошибка: {e}"


def needs_confirmation(entity_id: str, action: str, confirm_critical: bool) -> bool:
    if not confirm_critical:
        return False
    if action in ("unlock", "lock"):
        return True
    if is_critical(entity_id):
        return True
    return False
