"""Действия над entity."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..classifiers import is_critical
from ..ha_client import HAClient


def kb_entity_actions(
    entity_id: str, state: dict, short: str, back_to: str = "m",
    is_favorite: bool = False,
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

    if domain == "climate":
        # HVAC режимы
        hvac_icons = {
            "off": ("⚪", "Выкл"),
            "heat": ("🔥", "Нагрев"),
            "cool": ("❄", "Охлаж"),
            "heat_cool": ("🌡", "Авто"),
            "auto": ("🤖", "Авто"),
            "dry": ("💧", "Сушка"),
            "fan_only": ("🌪", "Вентил"),
        }
        cur_mode = state_val
        modes = attrs.get("hvac_modes") or []
        if modes:
            mode_btns = []
            for m in modes:
                icon, label = hvac_icons.get(m, ("•", m))
                mark = "✓ " if m == cur_mode else ""
                mode_btns.append(InlineKeyboardButton(
                    text=f"{mark}{icon} {label}",
                    callback_data=f"a:{short}:climate_mode:{m}"[:64],
                ))
            # bulk into rows of 3
            for i in range(0, len(mode_btns), 3):
                rows.append(mode_btns[i:i+3])

        # Температура: одна точка ИЛИ диапазон low/high
        cur_t = attrs.get("temperature")
        cur_low = attrs.get("target_temp_low")
        cur_high = attrs.get("target_temp_high")
        if cur_t is not None:
            rows.append([
                InlineKeyboardButton(text="-1°", callback_data=f"a:{short}:climate_temp:-1"),
                InlineKeyboardButton(text="-0.5°", callback_data=f"a:{short}:climate_temp:-0.5"),
                InlineKeyboardButton(text=f"{cur_t}°", callback_data="noop"),
                InlineKeyboardButton(text="+0.5°", callback_data=f"a:{short}:climate_temp:0.5"),
                InlineKeyboardButton(text="+1°", callback_data=f"a:{short}:climate_temp:1"),
            ])
        elif cur_low is not None or cur_high is not None:
            # Диапазонный режим (heat_cool с target_temp_low/high)
            if cur_low is not None:
                rows.append([
                    InlineKeyboardButton(text="🟦 -1°", callback_data=f"a:{short}:climate_temp_low:-1"),
                    InlineKeyboardButton(text="-0.5°", callback_data=f"a:{short}:climate_temp_low:-0.5"),
                    InlineKeyboardButton(text=f"низ {cur_low}°", callback_data="noop"),
                    InlineKeyboardButton(text="+0.5°", callback_data=f"a:{short}:climate_temp_low:0.5"),
                    InlineKeyboardButton(text="+1°", callback_data=f"a:{short}:climate_temp_low:1"),
                ])
            if cur_high is not None:
                rows.append([
                    InlineKeyboardButton(text="🟥 -1°", callback_data=f"a:{short}:climate_temp_high:-1"),
                    InlineKeyboardButton(text="-0.5°", callback_data=f"a:{short}:climate_temp_high:-0.5"),
                    InlineKeyboardButton(text=f"верх {cur_high}°", callback_data="noop"),
                    InlineKeyboardButton(text="+0.5°", callback_data=f"a:{short}:climate_temp_high:0.5"),
                    InlineKeyboardButton(text="+1°", callback_data=f"a:{short}:climate_temp_high:1"),
                ])

        # Preset modes (away / home / eco / boost / sleep)
        presets = attrs.get("preset_modes") or []
        cur_preset = attrs.get("preset_mode")
        if presets:
            p_btns = []
            for p in presets:
                mark = "✓ " if p == cur_preset else ""
                p_btns.append(InlineKeyboardButton(
                    text=f"{mark}{p}", callback_data=f"a:{short}:climate_preset:{p}"[:64],
                ))
            for i in range(0, len(p_btns), 3):
                rows.append(p_btns[i:i+3])

        # Fan modes
        fans = attrs.get("fan_modes") or []
        cur_fan = attrs.get("fan_mode")
        if fans:
            f_btns = []
            for fm in fans:
                mark = "✓ " if fm == cur_fan else ""
                f_btns.append(InlineKeyboardButton(
                    text=f"{mark}🌪 {fm}", callback_data=f"a:{short}:climate_fan:{fm}"[:64],
                ))
            for i in range(0, len(f_btns), 3):
                rows.append(f_btns[i:i+3])

    # Графики истории для numeric sensor
    if domain == "sensor":
        try:
            float(state_val)
            is_numeric = True
        except (ValueError, TypeError):
            is_numeric = False
        if is_numeric:
            rows.append([
                InlineKeyboardButton(text="📊 6 часов", callback_data=f"a:{short}:graph:6"),
                InlineKeyboardButton(text="📊 24 часа", callback_data=f"a:{short}:graph:24"),
                InlineKeyboardButton(text="📊 7 дней", callback_data=f"a:{short}:graph:168"),
            ])

    if domain == "media_player":
        # supported_features bitmask
        sf = attrs.get("supported_features") or 0
        SF_PAUSE        = 1
        SF_SEEK         = 2
        SF_VOLUME_SET   = 4
        SF_VOLUME_MUTE  = 8
        SF_PREVIOUS     = 16
        SF_NEXT         = 32
        SF_TURN_ON      = 128
        SF_TURN_OFF     = 256
        SF_VOLUME_STEP  = 1024
        SF_SELECT_SRC   = 2048
        SF_STOP         = 4096
        SF_PLAY         = 16384

        is_off = state_val in ("off", "standby", "unavailable", "unknown")

        # Питание
        power_row = []
        if is_off and (sf & SF_TURN_ON):
            power_row.append(InlineKeyboardButton(text="🔌 Включить", callback_data=f"a:{short}:on"))
        elif not is_off and (sf & SF_TURN_OFF):
            power_row.append(InlineKeyboardButton(text="⏻ Выключить", callback_data=f"a:{short}:off"))
        if power_row:
            rows.append(power_row)

        # Транспорт
        transport = []
        if sf & SF_PREVIOUS:
            transport.append(InlineKeyboardButton(text="⏮", callback_data=f"a:{short}:mp_prev"))
        if state_val == "playing" and (sf & SF_PAUSE):
            transport.append(InlineKeyboardButton(text="⏸", callback_data=f"a:{short}:mp_pause"))
        elif state_val != "playing" and (sf & SF_PLAY):
            transport.append(InlineKeyboardButton(text="▶", callback_data=f"a:{short}:mp_play"))
        if sf & SF_STOP:
            transport.append(InlineKeyboardButton(text="⏹", callback_data=f"a:{short}:mp_stop"))
        if sf & SF_NEXT:
            transport.append(InlineKeyboardButton(text="⏭", callback_data=f"a:{short}:mp_next"))
        if transport:
            rows.append(transport)

        # Громкость
        if sf & (SF_VOLUME_SET | SF_VOLUME_STEP):
            vol = attrs.get("volume_level")
            muted = attrs.get("is_volume_muted")
            vol_pct = f"{int(vol * 100)}%" if isinstance(vol, (int, float)) else "?"
            vol_row = []
            if sf & SF_VOLUME_MUTE:
                vol_row.append(InlineKeyboardButton(
                    text="🔊" if muted else "🔇",
                    callback_data=f"a:{short}:mp_mute",
                ))
            vol_row.extend([
                InlineKeyboardButton(text="-10", callback_data=f"a:{short}:mp_vol:-10"),
                InlineKeyboardButton(text=f"🔊 {vol_pct}", callback_data="noop"),
                InlineKeyboardButton(text="+10", callback_data=f"a:{short}:mp_vol:10"),
            ])
            rows.append(vol_row)

        # Источник
        if sf & SF_SELECT_SRC:
            cur_src = attrs.get("source") or "—"
            src_list = attrs.get("source_list") or []
            if src_list:
                rows.append([InlineKeyboardButton(
                    text=f"📺 Источник: {cur_src[:30]}",
                    callback_data=f"a:{short}:mp_src_list",
                )])

        # Sound mode (если есть осмысленный список — не плейсхолдер)
        sound_modes = attrs.get("sound_mode_list") or []
        is_placeholder = set(sound_modes) <= {"FactoryDefaults", "default", "Default"}
        if sound_modes and 2 <= len(sound_modes) <= 6 and not is_placeholder:
            cur = attrs.get("sound_mode")
            sm_btns = []
            for sm in sound_modes:
                mark = "✓ " if sm == cur else ""
                sm_btns.append(InlineKeyboardButton(
                    text=f"{mark}🎵 {sm}",
                    callback_data=f"a:{short}:mp_sm:{sm}"[:64],
                ))
            for i in range(0, len(sm_btns), 3):
                rows.append(sm_btns[i:i+3])

    if domain == "humidifier":
        cur_h = attrs.get("humidity")
        if cur_h is not None:
            rows.append([
                InlineKeyboardButton(text="-5%", callback_data=f"a:{short}:humidity:-5"),
                InlineKeyboardButton(text=f"{cur_h}%", callback_data="noop"),
                InlineKeyboardButton(text="+5%", callback_data=f"a:{short}:humidity:5"),
            ])
        for preset in attrs.get("available_modes") or []:
            mark = "✓ " if preset == attrs.get("mode") else ""
            rows.append([InlineKeyboardButton(
                text=f"{mark}⚙ {preset}",
                callback_data=f"a:{short}:hum_mode:{preset}"[:64],
            )])

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

    # camera: меню действий не показывается — при открытии камеры бот сразу
    # шлёт кадр (см. _send_camera_snapshot / cb_entity), кнопка «🔄 Обновить»
    # живёт прямо на фото.

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

    fav_text = "✩ Убрать из избранного" if is_favorite else "⭐ В избранное"
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"e:{short}"),
        InlineKeyboardButton(text=fav_text, callback_data=f"tf:{short}"),
    ])
    # Кнопка "🏠 Меню" не дублирует "← Назад", если они идентичны (back_to == "m")
    nav = [InlineKeyboardButton(text="← Назад", callback_data=back_to)]
    if back_to != "m":
        nav.append(InlineKeyboardButton(text="🏠 Меню", callback_data="m"))
    rows.append(nav)
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
        elif action.startswith("climate_mode:"):
            mode = action.split(":", 1)[1]
            await ha.call_service("climate", "set_hvac_mode", entity_id, {"hvac_mode": mode})
        elif action.startswith("climate_temp:"):
            delta = float(action.split(":", 1)[1])
            st = await ha.get_state(entity_id)
            cur = (st or {}).get("attributes", {}).get("temperature")
            if cur is None:
                return False, "Нет target_temperature"
            new_t = round(float(cur) + delta, 1)
            await ha.call_service("climate", "set_temperature", entity_id, {"temperature": new_t})
        elif action.startswith("climate_temp_low:") or action.startswith("climate_temp_high:"):
            kind, raw = action.split(":", 1)
            delta = float(raw)
            st = await ha.get_state(entity_id)
            attrs = (st or {}).get("attributes", {})
            cur_low = attrs.get("target_temp_low")
            cur_high = attrs.get("target_temp_high")
            if cur_low is None or cur_high is None:
                return False, "Нет target_temp_low/high"
            if kind == "climate_temp_low":
                new_low = round(float(cur_low) + delta, 1)
                new_high = float(cur_high)
                if new_low > new_high:
                    new_low = new_high
            else:
                new_high = round(float(cur_high) + delta, 1)
                new_low = float(cur_low)
                if new_high < new_low:
                    new_high = new_low
            await ha.call_service("climate", "set_temperature", entity_id,
                                  {"target_temp_low": new_low, "target_temp_high": new_high})
        elif action.startswith("climate_preset:"):
            preset = action.split(":", 1)[1]
            await ha.call_service("climate", "set_preset_mode", entity_id, {"preset_mode": preset})
        elif action.startswith("climate_fan:"):
            fm = action.split(":", 1)[1]
            await ha.call_service("climate", "set_fan_mode", entity_id, {"fan_mode": fm})
        elif action.startswith("humidity:"):
            delta = int(action.split(":", 1)[1])
            st = await ha.get_state(entity_id)
            cur = (st or {}).get("attributes", {}).get("humidity")
            if cur is None:
                return False, "Нет target humidity"
            new_h = max(0, min(100, int(cur) + delta))
            await ha.call_service("humidifier", "set_humidity", entity_id, {"humidity": new_h})
        elif action.startswith("hum_mode:"):
            mode = action.split(":", 1)[1]
            await ha.call_service("humidifier", "set_mode", entity_id, {"mode": mode})
        elif action == "mp_play":
            await ha.call_service("media_player", "media_play", entity_id)
        elif action == "mp_pause":
            await ha.call_service("media_player", "media_pause", entity_id)
        elif action == "mp_stop":
            await ha.call_service("media_player", "media_stop", entity_id)
        elif action == "mp_prev":
            await ha.call_service("media_player", "media_previous_track", entity_id)
        elif action == "mp_next":
            await ha.call_service("media_player", "media_next_track", entity_id)
        elif action == "mp_mute":
            st = await ha.get_state(entity_id)
            cur = (st or {}).get("attributes", {}).get("is_volume_muted", False)
            await ha.call_service("media_player", "volume_mute", entity_id,
                                  {"is_volume_muted": not cur})
        elif action.startswith("mp_vol:"):
            delta = int(action.split(":", 1)[1])
            st = await ha.get_state(entity_id)
            cur = (st or {}).get("attributes", {}).get("volume_level", 0) or 0
            new = max(0.0, min(1.0, float(cur) + delta / 100))
            await ha.call_service("media_player", "volume_set", entity_id,
                                  {"volume_level": round(new, 2)})
        elif action.startswith("mp_src:"):
            # Выбор источника по индексу: mp_src:<idx>
            idx = int(action.split(":", 1)[1])
            st = await ha.get_state(entity_id)
            src_list = (st or {}).get("attributes", {}).get("source_list") or []
            if 0 <= idx < len(src_list):
                await ha.call_service("media_player", "select_source", entity_id,
                                      {"source": src_list[idx]})
            else:
                return False, "Источник недоступен (список изменился)"
        elif action.startswith("mp_sm:"):
            sm = action.split(":", 1)[1]
            await ha.call_service("media_player", "select_sound_mode", entity_id,
                                  {"sound_mode": sm})
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
