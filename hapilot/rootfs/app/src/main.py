"""HAPilot — entry point."""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

from .classifiers import (
    BINARY_BY_DEVICE_CLASS,
    SENSOR_BY_DEVICE_CLASS,
    format_state,
    icon_for_area,
    label_for_domain,
)
from .favorites import FavoritesStore
from .ha_client import HAClient
from .live import Screen, ScreenRegistry, state_changed_loop
from .menus.actions import (
    execute_action,
    kb_confirm,
    kb_entity_actions,
    needs_confirmation,
)
from .menus.alerts import kb_alerts_problems, kb_alerts_root, kb_alerts_unavail
from .menus.domains import (
    SPLIT_BY_CLASS,
    kb_domain_classes,
    kb_domain_entities,
    kb_domains_root,
)
from .menus.favorites import kb_favorites
from .menus.rooms import (
    ROOM_SPLIT_DOMAINS,
    ROOM_SPLIT_THRESHOLD,
    kb_room_classes,
    kb_room_domain,
    kb_room_domains,
    kb_rooms_root,
)
from .visibility import VisibilityStore

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("hapilot")


def _h(s) -> str:
    """HTML-escape для безопасного embed user-content в parse_mode=HTML."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def update_message(cb, text: str, *, reply_markup=None, parse_mode: str = "HTML"):
    """Обновить текущее сообщение текстом + клавиатурой.
    Если текущее сообщение — фото (camera snapshot), удалить и отправить новое.
    """
    from aiogram.exceptions import TelegramBadRequest
    try:
        if cb.message.photo:
            await cb.message.delete()
            await cb.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await cb.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest:
        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


# Reply-клавиатура верхнего уровня (persistent, под полем ввода).
# Метки — единственный «ключ» reply-кнопок (callback_data у них нет),
# поэтому дублируются в роутере on_reply_nav ниже. Держать в синхроне.
BTN_ROOMS = "📍 Комнаты"
BTN_TYPES = "📋 Типы"
BTN_ALERTS = "🚨 Алерты"
BTN_FAVS = "⭐ Избранное"
BTN_SETTINGS = "⚙ Настройки"
BTN_CLOSE = "⬇️ Свернуть меню"
REPLY_NAV_LABELS = {BTN_ROOMS, BTN_TYPES, BTN_ALERTS, BTN_FAVS, BTN_SETTINGS, BTN_CLOSE}


def kb_reply_main(is_admin: bool, favs_count: int = 0) -> ReplyKeyboardMarkup:
    """Нижнее меню верхнего уровня. Вложенность — на inline.
    is_persistent=False → пользователь может свернуть клавиатуру родной
    «шапочкой» и выйти в список чатов; плюс явная кнопка «Свернуть»."""
    rows = [[KeyboardButton(text=BTN_ROOMS), KeyboardButton(text=BTN_TYPES)]]
    alerts_row = [KeyboardButton(text=BTN_ALERTS)]
    if favs_count > 0:
        alerts_row.append(KeyboardButton(text=BTN_FAVS))
    rows.append(alerts_row)
    last_row = [KeyboardButton(text=BTN_CLOSE)]
    if is_admin:
        last_row.insert(0, KeyboardButton(text=BTN_SETTINGS))
    rows.append(last_row)
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=False,
        input_field_placeholder="Меню внизу ↓ (⬇️ свернуть)",
    )


def kb_main_menu(is_admin: bool, edit_mode: bool, favs_count: int = 0) -> InlineKeyboardMarkup:
    rows = []
    if favs_count > 0:
        rows.append([InlineKeyboardButton(text=f"⭐ Избранное ({favs_count})", callback_data="f:")])
    rows.extend([
        [InlineKeyboardButton(text="📍 По комнатам", callback_data="r:")],
        [InlineKeyboardButton(text="📋 По типам", callback_data="d:")],
        [InlineKeyboardButton(text="🚨 Алерты", callback_data="al:")],
    ])
    if is_admin:
        label = "⚙ Настройки" + (" 🔧 (edit-mode ON)" if edit_mode else "")
        rows.append([InlineKeyboardButton(text=label, callback_data="s:")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_settings_menu(edit_mode: bool, vis: VisibilityStore) -> InlineKeyboardMarkup:
    hidden_n, forced_n = vis.stats
    toggle_text = "🔧 Выключить настройку видимости" if edit_mode else "👁 Включить настройку видимости"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data="s:edit")],
        [InlineKeyboardButton(text=f"♻ Сбросить overrides (скрыто:{hidden_n} / показ:{forced_n})", callback_data="s:reset")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="m")],
    ])


def is_allowed(user_id: int, allowed: set[int], admins: set[int] | None = None) -> bool:
    """Админ неявно allowed."""
    return user_id in allowed or (admins is not None and user_id in admins)


def is_admin(user_id: int, admins: set[int]) -> bool:
    return user_id in admins


async def main():
    load_dotenv()

    tg_token = os.environ.get("TG_BOT_TOKEN", "")
    ha_url = os.environ.get("HA_URL", "")
    ha_token = os.environ.get("HA_TOKEN", "")
    instance_name = os.environ.get("INSTANCE_NAME", "Дом")
    confirm_critical = os.environ.get("CONFIRM_CRITICAL", "true").lower() == "true"
    cache_ttl = int(os.environ.get("CACHE_TTL_SECONDS", "60"))
    allowed_user_ids = {
        int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
    }
    admin_user_ids_env = os.environ.get("ADMIN_USER_IDS", "").strip()
    if admin_user_ids_env:
        admin_user_ids = {int(x) for x in admin_user_ids_env.split(",") if x.strip()}
    else:
        # По умолчанию первый из allowed = админ
        admin_user_ids = {min(allowed_user_ids)} if allowed_user_ids else set()

    if not tg_token or not ha_token:
        raise SystemExit("Missing TG_BOT_TOKEN or HA_TOKEN")
    if not allowed_user_ids:
        log.warning("ALLOWED_USER_IDS пуст — никто не сможет пользоваться ботом")

    log.info(
        "instance=%s allowed_users=%d admins=%d ha_url=%s",
        instance_name, len(allowed_user_ids), len(admin_user_ids), ha_url,
    )

    ha = HAClient(ha_url, ha_token)
    vis = VisibilityStore(os.environ.get("VISIBILITY_PATH", "./data/visibility.json"))
    favs = FavoritesStore(os.environ.get("FAVORITES_PATH", "./data/favorites.json"))
    live_registry = ScreenRegistry()

    proxy_url = os.environ.get("TG_PROXY_URL", "").strip()
    if proxy_url:
        from aiogram.client.session.aiohttp import AiohttpSession
        log.info("Using Telegram proxy: %s", proxy_url)
        bot = Bot(token=tg_token, session=AiohttpSession(proxy=proxy_url))
    else:
        bot = Bot(token=tg_token)
    dp = Dispatcher()

    # short id ↔ entity_id cache (в памяти)
    id_cache: dict = {}
    # per-user edit mode flag
    edit_mode: dict[int, bool] = {}
    # id последнего inline-меню, открытого через нижнюю reply-кнопку —
    # чтобы удалять его при следующем тапе и не копить простыню
    last_menu_msg: dict[int, int] = {}

    def _main_kb(uid: int) -> InlineKeyboardMarkup:
        return kb_main_menu(is_admin(uid, admin_user_ids), edit_mode.get(uid, False), len(favs))

    def _register_live(cb: CallbackQuery, entity_ids, render_fn) -> None:
        """Зарегистрировать текущий экран как live — будет авто-обновляться."""
        if not cb.message:
            return
        live_registry.register(Screen(
            chat_id=cb.message.chat.id,
            message_id=cb.message.message_id,
            entity_ids=set(entity_ids),
            render=render_fn,
        ))

    async def _build_card(entity_id: str, short: str, back_to: str) -> tuple[str, InlineKeyboardMarkup]:
        """Собрать text + клавиатуру для карточки entity. Тянет свежий get_state."""
        st = await ha.get_state(entity_id) or {}
        attrs = st.get("attributes", {})
        fname = attrs.get("friendly_name", entity_id)
        state = st.get("state", "?")
        unit = attrs.get("unit_of_measurement", "")
        domain = entity_id.split(".", 1)[0]
        if entity_id.startswith("binary_sensor."):
            from .classifiers import binary_state_label
            bs_icon, bs_label = binary_state_label(entity_id, attrs, str(state))
            state_render = f"{bs_icon} {bs_label}"
        else:
            state_render = f"{_h(format_state(state, attrs))}{_h(str(unit))}"
        text = f"<b>{_h(fname)}</b>\n<code>{_h(entity_id)}</code>\nСостояние: <code>{state_render}</code>"
        if attrs.get("preset_mode"):
            text += f"\nРежим: <code>{_h(str(attrs['preset_mode']))}</code>"
        if "brightness" in attrs and attrs["brightness"]:
            pct = round(attrs["brightness"] / 255 * 100)
            text += f"\nЯркость: <code>{pct}%</code>"
        if domain == "climate":
            cur_t = attrs.get("current_temperature")
            tgt_t = attrs.get("temperature")
            tgt_low = attrs.get("target_temp_low")
            tgt_high = attrs.get("target_temp_high")
            tunit = attrs.get("temperature_unit", "°C")
            if cur_t is not None:
                text += f"\nТекущая: <code>{cur_t}{_h(tunit)}</code>"
            if tgt_t is not None:
                text += f"\nЦель: <code>{tgt_t}{_h(tunit)}</code>"
            elif tgt_low is not None and tgt_high is not None:
                text += f"\nДиапазон: <code>{tgt_low}–{tgt_high}{_h(tunit)}</code>"
            cur_h = attrs.get("current_humidity")
            if cur_h is not None:
                text += f"\nВлажность: <code>{cur_h}%</code>"
            if attrs.get("hvac_action"):
                text += f"\nДействие: <code>{_h(str(attrs['hvac_action']))}</code>"
            if attrs.get("fan_mode"):
                text += f"\nВентиляция: <code>{_h(str(attrs['fan_mode']))}</code>"
        if domain == "media_player":
            mt = attrs.get("media_title")
            ma = attrs.get("media_artist")
            mab = attrs.get("media_album_name")
            src = attrs.get("source")
            vol = attrs.get("volume_level")
            muted = attrs.get("is_volume_muted")
            if mt:
                line = f"\n🎵 <b>{_h(str(mt))}</b>"
                if ma:
                    line += f"\n   <i>{_h(str(ma))}</i>"
                if mab and mab != ma:
                    line += f"\n   <code>{_h(str(mab))}</code>"
                text += line
            if src:
                text += f"\nИсточник: <code>{_h(str(src))}</code>"
            if isinstance(vol, (int, float)):
                vmark = "🔇" if muted else "🔊"
                text += f"\n{vmark} Громкость: <code>{int(vol*100)}%</code>"

        if domain == "humidifier":
            tgt_h = attrs.get("humidity")
            cur_h = attrs.get("current_humidity")
            if cur_h is not None:
                text += f"\nТекущая влажность: <code>{cur_h}%</code>"
            if tgt_h is not None:
                text += f"\nЦель: <code>{tgt_h}%</code>"
            if attrs.get("mode"):
                text += f"\nРежим: <code>{_h(str(attrs['mode']))}</code>"
        kb = kb_entity_actions(entity_id, st, short, back_to=back_to, is_favorite=(entity_id in favs))
        return text, kb

    async def _safe_edit(chat_id: int, message_id: int, text: str, kb: InlineKeyboardMarkup) -> None:
        """edit_message_text с глушением "message not modified" и недоступности."""
        from aiogram.exceptions import TelegramBadRequest
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id,
                reply_markup=kb, parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                log.debug("safe_edit: %s (chat=%s msg=%s)", e, chat_id, message_id)
                # экран всё, разрегаем
                live_registry.unregister(chat_id, message_id)

    @dp.message(Command("start"))
    async def cmd_start(msg: Message):
        if not is_allowed(msg.from_user.id, allowed_user_ids, admin_user_ids):
            await msg.reply("Доступ запрещён.")
            log.warning("denied access user_id=%s", msg.from_user.id)
            return
        uid = msg.from_user.id
        await msg.reply(
            f"🏠 <b>{_h(instance_name)}</b>\nМеню внизу — выбирай раздел.",
            reply_markup=kb_reply_main(is_admin(uid, admin_user_ids), len(favs)),
            parse_mode="HTML",
        )

    @dp.message(F.text.in_(REPLY_NAV_LABELS))
    async def on_reply_nav(msg: Message):
        """Роутер нижних reply-кнопок → открывает inline-раздел новым сообщением.
        Вложенная навигация дальше остаётся на inline (edit-in-place + live)."""
        uid = msg.from_user.id
        if not is_allowed(uid, allowed_user_ids, admin_user_ids):
            return await msg.reply("Доступ запрещён.")
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception:
            return await msg.reply("⚠ HA сейчас недоступен. Попробуй через минуту.")
        chat_id = msg.chat.id
        # Чистим прошлое inline-меню и сам тап-текст, чтобы не копить простыню.
        prev = last_menu_msg.pop(uid, None)
        if prev:
            try:
                await bot.delete_message(chat_id, prev)
            except Exception:
                pass
        try:
            await msg.delete()
        except Exception:
            pass

        t = msg.text
        em = edit_mode.get(uid, False)
        sent: Message | None = None
        if t == BTN_CLOSE:
            # Убрать нижнюю клавиатуру, чтобы можно было выйти в список чатов
            await msg.answer("Меню свёрнуто. /start — вернуть.", reply_markup=ReplyKeyboardRemove())
            return
        if t == BTN_ROOMS:
            suffix = " 🔧" if em else ""
            sent = await msg.answer(
                f"🏠 <b>Комнаты</b>{suffix}",
                reply_markup=kb_rooms_root(snap, vis, em), parse_mode="HTML",
            )
        elif t == BTN_TYPES:
            sent = await msg.answer(
                "📋 <b>По типам</b>",
                reply_markup=kb_domains_root(snap, vis), parse_mode="HTML",
            )
        elif t == BTN_ALERTS:
            try:
                ws_url = ha.url.replace("http", "ws", 1) + "/api/websocket"
                import json as _json

                import websockets
                async with websockets.connect(ws_url) as ws:
                    await ws.recv()
                    await ws.send(_json.dumps({"type": "auth", "access_token": ha.token}))
                    await ws.recv()
                    await ws.send(_json.dumps({"id": 1, "type": "persistent_notification/get"}))
                    notes = _json.loads(await ws.recv()).get("result", [])
                notif_count = len(notes)
            except Exception:
                notif_count = 0
            sent = await msg.answer(
                "🚨 <b>Алерты</b>",
                reply_markup=kb_alerts_root(snap, notif_count), parse_mode="HTML",
            )
        elif t == BTN_FAVS:
            if len(favs) == 0:
                sent = await msg.answer("Избранное пусто. Открой entity и нажми ⭐ чтобы добавить.")
            else:
                sent = await msg.answer(
                    "⭐ <b>Избранное</b>",
                    reply_markup=kb_favorites(snap, favs, id_cache), parse_mode="HTML",
                )
        elif t == BTN_SETTINGS:
            if not is_admin(uid, admin_user_ids):
                sent = await msg.answer("Только для админа.")
            else:
                hidden_n, forced_n = vis.stats
                text = (
                    f"⚙ <b>Настройки</b>\n\n"
                    f"Edit-mode: <b>{'ON 🔧' if em else 'OFF'}</b>\n"
                    f"Принудительно скрыто: <b>{hidden_n}</b>\n"
                    f"Принудительно показано: <b>{forced_n}</b>\n\n"
                    f"В edit-mode заходи в комнаты и тыкай ✅/🙈 у каждого устройства."
                )
                sent = await msg.answer(text, reply_markup=kb_settings_menu(em, vis), parse_mode="HTML")

        if sent is not None:
            last_menu_msg[uid] = sent.message_id

    @dp.callback_query(F.data == "m")
    async def cb_main(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        await update_message(cb, 
            f"🏠 <b>{_h(instance_name)}</b>\nВыберите способ управления:",
            reply_markup=_main_kb(cb.from_user.id),
            parse_mode="HTML",
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("s:"))
    async def cb_settings(cb: CallbackQuery):
        if not is_admin(cb.from_user.id, admin_user_ids):
            return await cb.answer("Только для админа", show_alert=True)
        sub = cb.data[2:]
        uid = cb.from_user.id
        if sub == "edit":
            edit_mode[uid] = not edit_mode.get(uid, False)
            log.info("user=%s edit_mode=%s", uid, edit_mode[uid])
            await cb.answer(f"Edit-mode: {'ON' if edit_mode[uid] else 'OFF'}")
        elif sub == "reset":
            vis._hidden.clear(); vis._forced_visible.clear(); vis.save()
            await cb.answer("Overrides сброшены", show_alert=True)
        em = edit_mode.get(uid, False)
        hidden_n, forced_n = vis.stats
        text = (
            f"⚙ <b>Настройки</b>\n\n"
            f"Edit-mode: <b>{'ON 🔧' if em else 'OFF'}</b>\n"
            f"Принудительно скрыто: <b>{hidden_n}</b>\n"
            f"Принудительно показано: <b>{forced_n}</b>\n\n"
            f"В edit-mode заходи в комнаты и тыкай ✅/🙈 у каждого устройства."
        )
        await update_message(cb, text, reply_markup=kb_settings_menu(em, vis), parse_mode="HTML")

    @dp.callback_query(F.data == "noop")
    async def cb_noop(cb: CallbackQuery):
        await cb.answer()

    @dp.callback_query(F.data == "f:")
    async def cb_favorites(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception:
            return await cb.answer("⚠ HA недоступен", show_alert=True)
        if len(favs) == 0:
            return await cb.answer("Избранное пусто. Открой entity и нажми ⭐ чтобы добавить.", show_alert=True)
        await update_message(cb, "⭐ <b>Избранное</b>",
                             reply_markup=kb_favorites(snap, favs, id_cache),
                             parse_mode="HTML")
        # Live для всех текущих избранных entity
        chat_id = cb.message.chat.id
        message_id = cb.message.message_id
        async def _render():
            s = ha.snapshot
            await _safe_edit(chat_id, message_id, "⭐ <b>Избранное</b>",
                             kb_favorites(s, favs, id_cache))
        _register_live(cb, list(favs.items), _render)
        await cb.answer()

    @dp.callback_query(F.data.startswith("tf:"))
    async def cb_toggle_fav(cb: CallbackQuery):
        """Toggle favorite: tf:<short>."""
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        short = cb.data.split(":", 1)[1]
        eid = id_cache.get(short)
        if not eid:
            return await cb.answer("Сессия устарела, нажми /start", show_alert=True)
        now_fav = favs.toggle(eid)
        await cb.answer(f"{'⭐ Добавлено в избранное' if now_fav else '✩ Убрано из избранного'}")
        log.info("favorite user=%s entity=%s state=%s", cb.from_user.id, eid, now_fav)
        # перерисовать карточку — ⭐/✩ обновится
        cb.data = f"e:{short}"  # type: ignore
        await cb_entity(cb)

    @dp.callback_query(F.data.startswith("d:"))
    async def cb_domains(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception:
            return await cb.answer("⚠ HA недоступен", show_alert=True)
        sub = cb.data[2:]
        if sub == "":
            await update_message(cb, "📋 <b>По типам</b>",
                                 reply_markup=kb_domains_root(snap, vis),
                                 parse_mode="HTML")
        else:
            # Формат: domain[:dc_or_all][:pN]
            tokens = sub.split(":")
            domain = tokens[0]
            device_class = None
            page = 1
            for t in tokens[1:]:
                if t.startswith("p") and t[1:].isdigit():
                    page = int(t[1:])
                elif t == "_all":
                    device_class = None
                else:
                    device_class = t

            # sensor/binary_sensor без device_class → подменю классов
            if device_class is None and domain in SPLIT_BY_CLASS and page == 1:
                title, kb = kb_domain_classes(snap, domain, vis)
                await update_message(cb, title, reply_markup=kb, parse_mode="HTML")
                await cb.answer()
                return

            title, kb = kb_domain_entities(snap, domain, id_cache, vis, device_class, page=page)
            await update_message(cb, title, reply_markup=kb, parse_mode="HTML")
            # Live: entity домена + (опц.) фильтр по классу
            def _matches(e: dict) -> bool:
                if not e["entity_id"].startswith(domain + "."):
                    return False
                if e.get("disabled_by") or not vis.is_visible(e):
                    return False
                if device_class is None:
                    return True
                st = snap.state(e["entity_id"]) or {}
                dc = st.get("attributes", {}).get("device_class") or ""
                return (device_class == "_other" and not dc) or (dc == device_class)
            entity_ids = [e["entity_id"] for e in snap.entities if _matches(e)]
            chat_id = cb.message.chat.id
            message_id = cb.message.message_id
            async def _render():
                s = ha.snapshot
                t, k = kb_domain_entities(s, domain, id_cache, vis, device_class, page=page)
                await _safe_edit(chat_id, message_id, t, k)
            _register_live(cb, entity_ids, _render)
        await cb.answer()

    @dp.callback_query(F.data.startswith("al:"))
    async def cb_alerts(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception:
            return await cb.answer("⚠ HA недоступен", show_alert=True)
        sub = cb.data[3:]

        # Quick actions
        if sub == "water_off":
            try:
                await ha.call_service("switch", "turn_off", "switch.water")
                await ha.call_service("switch", "turn_off", "switch.boiler")
                await cb.answer("🚫 Вода и бойлер перекрыты", show_alert=True)
            except Exception as e:
                await cb.answer(f"⚠ Ошибка: {e}", show_alert=True)
            await ha.refresh_snapshot(cache_ttl, force=True)
            cb.data = "al:problems"
            return await cb_alerts(cb)
        if sub == "water_on":
            try:
                await ha.call_service("switch", "turn_on", "switch.water")
                await ha.call_service("switch", "turn_on", "switch.boiler")
                await cb.answer("💧 Вода и бойлер открыты", show_alert=True)
            except Exception as e:
                await cb.answer(f"⚠ Ошибка: {e}", show_alert=True)
            await ha.refresh_snapshot(cache_ttl, force=True)
            cb.data = "al:problems"
            return await cb_alerts(cb)
        if sub == "ack_smoke":
            try:
                await ha.call_service("automation", "turn_off", "automation.zadymlenie")
                await cb.answer("🔕 Алярм дыма заглушен на 5 мин", show_alert=True)
                # через 5 мин включить обратно — через HA-таймер не дёргаем,
                # просто планируем delayed call
                async def _resume_smoke():
                    await asyncio.sleep(300)
                    try:
                        await ha.call_service("automation","turn_on","automation.zadymlenie")
                        log.info("zadymlenie automation re-enabled after silence")
                    except Exception as e:
                        log.warning("failed to re-enable zadymlenie: %s", e)
                asyncio.create_task(_resume_smoke())
            except Exception as e:
                await cb.answer(f"⚠ Ошибка: {e}", show_alert=True)
            cb.data = "al:problems"
            return await cb_alerts(cb)
        if sub == "ack_leak":
            try:
                await ha.call_service("automation", "turn_off", "automation.protechka_vody")
                await cb.answer("🔕 Алярм протечки заглушен на 5 мин", show_alert=True)
                async def _resume_leak():
                    await asyncio.sleep(300)
                    try:
                        await ha.call_service("automation","turn_on","automation.protechka_vody")
                        log.info("protechka_vody automation re-enabled after silence")
                    except Exception as e:
                        log.warning("failed to re-enable protechka_vody: %s", e)
                asyncio.create_task(_resume_leak())
            except Exception as e:
                await cb.answer(f"⚠ Ошибка: {e}", show_alert=True)
            cb.data = "al:problems"
            return await cb_alerts(cb)

        if sub == "":
            # Корень — главное меню алертов с счётчиками
            try:
                ws_url = ha.url.replace("http","ws",1) + "/api/websocket"
                import websockets, json as _json
                async with websockets.connect(ws_url) as ws:
                    await ws.recv()
                    await ws.send(_json.dumps({"type":"auth","access_token":ha.token}))
                    await ws.recv()
                    await ws.send(_json.dumps({"id":1,"type":"persistent_notification/get"}))
                    notes = _json.loads(await ws.recv()).get("result",[])
                notif_count = len(notes)
            except Exception:
                notif_count = 0
            await update_message(cb, 
                f"🚨 <b>Алерты</b>",
                reply_markup=kb_alerts_root(snap, notif_count),
                parse_mode="HTML",
            )
        elif sub == "problems":
            text, kb = kb_alerts_problems(snap)
            await update_message(cb, text, reply_markup=kb, parse_mode="HTML")
        elif sub == "unavail":
            text, kb = kb_alerts_unavail(snap)
            await update_message(cb, text, reply_markup=kb, parse_mode="HTML")
        elif sub == "notif":
            try:
                ws_url = ha.url.replace("http","ws",1) + "/api/websocket"
                import websockets, json as _json
                async with websockets.connect(ws_url) as ws:
                    await ws.recv()
                    await ws.send(_json.dumps({"type":"auth","access_token":ha.token}))
                    await ws.recv()
                    await ws.send(_json.dumps({"id":1,"type":"persistent_notification/get"}))
                    notes = _json.loads(await ws.recv()).get("result",[])
            except Exception:
                notes = []
            if not notes:
                text = "📋 <b>Уведомления HA</b>\n\nНет активных уведомлений. ✅"
            else:
                lines = [f"📋 <b>Уведомления HA ({len(notes)})</b>", ""]
                for n in notes[:15]:
                    title = _h(n.get("title", "(без заголовка)"))
                    msg = _h((n.get("message", "") or "")[:300])
                    when = n.get("created_at","")[:19].replace("T"," ")
                    lines.append(f"<b>{title}</b>\n<i>{when}</i>\n{msg}\n")
                text = "\n".join(lines)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="← Алерты", callback_data="al:")
            ]])
            await update_message(cb, text, reply_markup=kb, parse_mode="HTML")
        await cb.answer()

    @dp.callback_query(F.data.startswith("r:"))
    async def cb_rooms(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception as e:
            log.warning("snapshot fetch failed in cb_rooms: %s", e)
            return await cb.answer("⚠ HA сейчас недоступен. Попробуй через минуту.", show_alert=True)
        if not snap.areas and not snap.entities:
            return await cb.answer("⚠ HA не ответил, повтори позже.", show_alert=True)
        parts = cb.data.split(":")
        # r:                → root
        # r:<area>          → domains
        # r:<area>:<domain> → entities
        em = edit_mode.get(cb.from_user.id, False)
        suffix = " 🔧" if em else ""
        if len(parts) == 2 and parts[1] == "":
            await update_message(cb, 
                f"🏠 <b>Комнаты</b>{suffix}", reply_markup=kb_rooms_root(snap, vis, em), parse_mode="HTML"
            )
        elif len(parts) == 2:
            area_id = parts[1]
            area_name = next(
                (a["name"] for a in snap.areas if a["area_id"] == area_id),
                area_id,
            )
            await update_message(cb, 
                f"{icon_for_area(area_name)} <b>{_h(area_name)}</b>{suffix}",
                reply_markup=kb_room_domains(snap, area_id, vis, em),
                parse_mode="HTML",
            )
        elif len(parts) == 3:
            area_id, domain = parts[1], parts[2]
            area_name = next(
                (a["name"] for a in snap.areas if a["area_id"] == area_id),
                area_id,
            )
            # Если в комнате слишком много sensor/binary_sensor — показываем подменю классов
            if domain in ROOM_SPLIT_DOMAINS:
                ent_count = sum(
                    1 for e in snap.entities_in_area(area_id)
                    if e["entity_id"].startswith(domain + ".")
                    and not e.get("disabled_by")
                    and (em or vis.is_visible(e))
                )
                if ent_count > ROOM_SPLIT_THRESHOLD:
                    title = f"{icon_for_area(area_name)} <b>{_h(area_name)}</b> / {_h(label_for_domain(domain))}{suffix} — выбери класс"
                    await update_message(cb, title,
                        reply_markup=kb_room_classes(snap, area_id, domain, vis, em),
                        parse_mode="HTML",
                    )
                    await cb.answer()
                    return
            title = f"{icon_for_area(area_name)} <b>{_h(area_name)}</b> / {_h(domain)}{suffix}"
            await update_message(cb, title,
                reply_markup=kb_room_domain(snap, area_id, domain, id_cache, vis, em),
                parse_mode="HTML",
            )
            # Live: все entity этого домена в этой area, видимые
            entity_ids = [
                e["entity_id"] for e in snap.entities_in_area(area_id)
                if e["entity_id"].startswith(domain + ".")
                and not e.get("disabled_by")
                and (em or vis.is_visible(e))
            ]
            chat_id = cb.message.chat.id
            message_id = cb.message.message_id
            async def _render():
                s = ha.snapshot
                kb = kb_room_domain(s, area_id, domain, id_cache, vis, em)
                await _safe_edit(chat_id, message_id, title, kb)
            _register_live(cb, entity_ids, _render)
        elif len(parts) >= 4:
            # r:<area>:<domain>:<dc_or_all>[:pN]
            area_id, domain, dc_token = parts[1], parts[2], parts[3]
            page = 1
            for t in parts[4:]:
                if t.startswith("p") and t[1:].isdigit():
                    page = int(t[1:])
            dc = None if dc_token == "_all" else dc_token
            area_name = next(
                (a["name"] for a in snap.areas if a["area_id"] == area_id),
                area_id,
            )
            table = SENSOR_BY_DEVICE_CLASS if domain == "sensor" else BINARY_BY_DEVICE_CLASS
            if dc is None:
                dc_label = label_for_domain(domain)
            elif dc == "_other":
                dc_label = "Прочее"
            elif dc in table:
                dc_label = table[dc][1]
            else:
                dc_label = dc
            title = f"{icon_for_area(area_name)} <b>{_h(area_name)}</b> / {_h(dc_label)}{suffix}"
            await update_message(cb, title,
                reply_markup=kb_room_domain(snap, area_id, domain, id_cache, vis, em, device_class=dc, page=page),
                parse_mode="HTML",
            )
            # Live: entity класса (или все если dc is None)
            def _matches_dc(e: dict) -> bool:
                if not e["entity_id"].startswith(domain + "."):
                    return False
                if e.get("disabled_by") or not (em or vis.is_visible(e)):
                    return False
                if dc is None:
                    return True
                st = snap.state(e["entity_id"]) or {}
                this_dc = st.get("attributes", {}).get("device_class") or ""
                return (dc == "_other" and not this_dc) or (this_dc == dc)
            entity_ids = [e["entity_id"] for e in snap.entities_in_area(area_id) if _matches_dc(e)]
            chat_id = cb.message.chat.id
            message_id = cb.message.message_id
            async def _render():
                s = ha.snapshot
                kb = kb_room_domain(s, area_id, domain, id_cache, vis, em, device_class=dc, page=page)
                await _safe_edit(chat_id, message_id, title, kb)
            _register_live(cb, entity_ids, _render)
        await cb.answer()

    @dp.callback_query(F.data.startswith("ba:"))
    async def cb_bulk_action(cb: CallbackQuery):
        """Массовое действие: ba:<area_id>:<domain>:<on|off>."""
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            _, area_id, domain, action = cb.data.split(":", 3)
        except ValueError:
            return await cb.answer("Битый callback", show_alert=True)
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception:
            return await cb.answer("⚠ HA недоступен", show_alert=True)

        targets = [
            e["entity_id"] for e in snap.entities_in_area(area_id)
            if e["entity_id"].startswith(domain + ".")
            and not e.get("disabled_by")
            and vis.is_visible(e)
        ]
        if not targets:
            return await cb.answer("Нет устройств", show_alert=True)

        service = "turn_on" if action == "on" else "turn_off"
        ok = 0
        errors: list[str] = []
        for eid in targets:
            try:
                await ha.call_service(domain, service, eid)
                ok += 1
            except Exception as e:
                errors.append(f"{eid}: {e}")
                log.warning("bulk %s/%s on %s failed: %s", domain, service, eid, e)

        verb = "включил" if action == "on" else "выключил"
        if errors:
            await cb.answer(f"⚠ {verb} {ok}/{len(targets)}, ошибок: {len(errors)}", show_alert=True)
        else:
            await cb.answer(f"✓ {verb} {ok} шт.")
        log.info("bulk user=%s area=%s domain=%s action=%s ok=%d/%d",
                 cb.from_user.id, area_id, domain, action, ok, len(targets))

        # Перерисовать список с обновлёнными статусами
        await ha.refresh_snapshot(cache_ttl, force=True)
        em = edit_mode.get(cb.from_user.id, False)
        suffix = " 🔧" if em else ""
        snap = ha.snapshot
        area_name = next(
            (a["name"] for a in snap.areas if a["area_id"] == area_id),
            area_id,
        )
        try:
            await update_message(
                cb,
                f"{icon_for_area(area_name)} <b>{_h(area_name)}</b> / {_h(domain)}{suffix}",
                reply_markup=kb_room_domain(snap, area_id, domain, id_cache, vis, em),
                parse_mode="HTML",
            )
        except Exception:
            pass

    async def _redraw_after_quick(cb: CallbackQuery, short: str) -> None:
        """Перерисовать тот же экран, с которого нажали быстрое вкл/выкл.

        Пользователь остаётся там, где был: та же комната, тот же фильтр и
        страница — иначе после каждого тапа его выбрасывает в начало списка.
        """
        await ha.refresh_snapshot(cache_ttl, force=True)
        snap = ha.snapshot
        em = edit_mode.get(cb.from_user.id, False)
        suffix = " 🔧" if em else ""
        view = id_cache.get("_view", {}).get(short)
        try:
            if view and view[0] == "dom":
                # Экран «по типам»: заголовок собирает сама kb_domain_entities
                _, domain, dc, page = view
                title, kb = kb_domain_entities(
                    ha.snapshot, domain, id_cache, vis, dc, page=page
                )
                await update_message(cb, title, reply_markup=kb, parse_mode="HTML")
            elif view and view[0] == "room":
                _, area_id, domain, dc, page = view
                area_name = next(
                    (a["name"] for a in snap.areas if a["area_id"] == area_id), area_id
                )
                await update_message(
                    cb,
                    f"{icon_for_area(area_name)} <b>{_h(area_name)}</b> / {_h(domain)}{suffix}",
                    reply_markup=kb_room_domain(
                        snap, area_id, domain, id_cache, vis, em,
                        device_class=dc, page=page,
                    ),
                    parse_mode="HTML",
                )
            elif id_cache.get("_parent", {}).get(short) == "f:":
                await update_message(
                    cb, "⭐ <b>Избранное</b>",
                    reply_markup=kb_favorites(snap, favs, id_cache),
                    parse_mode="HTML",
                )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("q:"))
    async def cb_quick_toggle(cb: CallbackQuery):
        """Быстрое вкл/выкл из списка: q:<short>:<on|off> — без захода в карточку."""
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            _, short, action = cb.data.split(":", 2)
        except ValueError:
            return await cb.answer("Битый callback", show_alert=True)
        if action not in ("on", "off"):
            return await cb.answer("Битый callback", show_alert=True)
        eid = id_cache.get(short)
        if not eid:
            return await cb.answer("Сессия устарела, открой меню заново", show_alert=True)

        domain = eid.split(".", 1)[0]
        service = "turn_on" if action == "on" else "turn_off"
        try:
            await ha.call_service(domain, service, eid)
        except Exception as e:
            log.warning("quick %s on %s failed: %s", service, eid, e)
            return await cb.answer(f"⚠ Не вышло: {e}", show_alert=True)

        await cb.answer("✓ включил" if action == "on" else "✓ выключил")
        log.info("quick user=%s entity=%s action=%s", cb.from_user.id, eid, action)
        await _redraw_after_quick(cb, short)

    @dp.callback_query(F.data.startswith("bx:"))
    async def cb_room_lights(cb: CallbackQuery):
        """Весь свет комнаты со списка комнат: bx:<area_id>:<on|off>.

        Отличие от ba: перерисовка остаётся на списке комнат — пользователь
        гасит свет в нескольких комнатах подряд, не проваливаясь внутрь.
        """
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        try:
            _, area_id, action = cb.data.split(":", 2)
        except ValueError:
            return await cb.answer("Битый callback", show_alert=True)
        if action not in ("on", "off"):
            return await cb.answer("Битый callback", show_alert=True)
        try:
            snap = await ha.refresh_snapshot(cache_ttl)
        except Exception:
            return await cb.answer("⚠ HA недоступен", show_alert=True)

        targets = [
            e["entity_id"] for e in snap.entities_in_area(area_id)
            if e["entity_id"].startswith("light.")
            and not e.get("disabled_by")
            and vis.is_visible(e)
        ]
        if not targets:
            return await cb.answer("В комнате нет света", show_alert=True)

        service = "turn_on" if action == "on" else "turn_off"
        ok = 0
        for eid in targets:
            try:
                await ha.call_service("light", service, eid)
                ok += 1
            except Exception as e:
                log.warning("room lights %s on %s failed: %s", service, eid, e)

        verb = "включил" if action == "on" else "выключил"
        if ok < len(targets):
            await cb.answer(f"⚠ {verb} {ok}/{len(targets)}", show_alert=True)
        else:
            await cb.answer(f"✓ {verb} свет ({ok})")
        log.info("room-lights user=%s area=%s action=%s ok=%d/%d",
                 cb.from_user.id, area_id, action, ok, len(targets))

        await ha.refresh_snapshot(cache_ttl, force=True)
        em = edit_mode.get(cb.from_user.id, False)
        suffix = " 🔧" if em else ""
        try:
            await update_message(
                cb, f"🏠 <b>Комнаты</b>{suffix}",
                reply_markup=kb_rooms_root(ha.snapshot, vis, em),
                parse_mode="HTML",
            )
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("v:"))
    async def cb_visibility_toggle(cb: CallbackQuery):
        if not is_admin(cb.from_user.id, admin_user_ids):
            return await cb.answer("Только админ", show_alert=True)
        if not edit_mode.get(cb.from_user.id, False):
            return await cb.answer("Включи edit-mode в настройках", show_alert=True)
        short = cb.data.split(":", 1)[1]
        eid = id_cache.get(short)
        if not eid:
            return await cb.answer("Сессия устарела", show_alert=True)
        snap = ha.snapshot
        ent = next((e for e in snap.entities if e["entity_id"] == eid), None)
        if not ent:
            return await cb.answer("Entity не найдена", show_alert=True)
        new_visible = vis.toggle(ent)
        await cb.answer(f"{'✅ Видна' if new_visible else '🙈 Скрыта'}: {eid}")
        # перерисовать текущий экран (parent path)
        parent = id_cache.get("_parent", {}).get(short, "r:")
        # эмулируем нажатие parent callback
        cb_data_orig = cb.data
        cb.data = parent  # type: ignore
        await cb_rooms(cb)
        cb.data = cb_data_orig  # type: ignore

    async def _send_camera_snapshot(cb: CallbackQuery, entity_id: str, short: str) -> None:
        """Сделать снимок камеры и показать его на месте текущего сообщения.
        Кнопки: «🔄 Обновить» (пере-снимок) + «← Назад». Общий путь для
        открытия камеры (мгновенное фото) и для кнопки «Обновить».
        Live-экран НЕ регистрируем — иначе авто-рендер вернул бы текст."""
        from aiogram.types import InputMediaPhoto
        await cb.answer("📸 Делаю снимок…")
        try:
            jpeg = await ha.camera_snapshot(entity_id)
        except Exception as e:
            log.warning("camera snapshot failed: %s", e)
            await cb.answer(f"⚠ Не удалось получить кадр: {e}", show_alert=True)
            return
        if not jpeg:
            await cb.answer("⚠ Камера не вернула кадр", show_alert=True)
            return
        back_to = id_cache.get("_parent", {}).get(short, "m")
        kb_photo = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"a:{short}:snapshot")],
            [InlineKeyboardButton(text="← Назад", callback_data=back_to)],
        ])
        fname = (await ha.get_state(entity_id) or {}).get("attributes", {}).get("friendly_name", entity_id)
        photo = BufferedInputFile(jpeg, filename=f"{entity_id}.jpg")
        try:
            if cb.message.photo:
                await cb.message.edit_media(
                    media=InputMediaPhoto(media=photo, caption=f"📷 {fname}"),
                    reply_markup=kb_photo,
                )
            else:
                await cb.message.delete()
                await cb.message.answer_photo(photo, caption=f"📷 {fname}", reply_markup=kb_photo)
        except Exception as e:
            log.warning("camera snapshot edit failed: %s", e)
            await cb.message.answer_photo(photo, caption=f"📷 {fname}", reply_markup=kb_photo)

    @dp.callback_query(F.data.startswith("e:"))
    async def cb_entity(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        short = cb.data.split(":", 1)[1]
        entity_id = id_cache.get(short)
        if not entity_id:
            return await cb.answer("Сессия устарела, нажмите /start", show_alert=True)
        # Камера: без промежуточного меню — сразу кадр (кнопка «Обновить» на фото)
        if entity_id.startswith("camera."):
            return await _send_camera_snapshot(cb, entity_id, short)
        back_to = id_cache.get("_parent", {}).get(short, "m")
        text, kb = await _build_card(entity_id, short, back_to)
        await update_message(cb, text, reply_markup=kb, parse_mode="HTML")
        # Регистрируем экран как live — будет авто-обновляться при state_changed
        chat_id = cb.message.chat.id
        message_id = cb.message.message_id
        async def _render():
            t, k = await _build_card(entity_id, short, back_to)
            await _safe_edit(chat_id, message_id, t, k)
        _register_live(cb, [entity_id], _render)
        await cb.answer()

    @dp.callback_query(F.data.startswith("a:"))
    async def cb_action(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        _, short, *rest = cb.data.split(":")
        action = ":".join(rest)
        entity_id = id_cache.get(short)
        if not entity_id:
            return await cb.answer("Сессия устарела", show_alert=True)
        log.info("action user=%s entity=%s action=%s", cb.from_user.id, entity_id, action)

        # Спец-кейс: подменю выбора источника media_player
        if action == "mp_src_list":
            st = await ha.get_state(entity_id) or {}
            attrs = st.get("attributes", {})
            cur = attrs.get("source") or ""
            src_list = attrs.get("source_list") or []
            if not src_list:
                return await cb.answer("Список источников пуст", show_alert=True)
            rows: list[list[InlineKeyboardButton]] = []
            row: list[InlineKeyboardButton] = []
            for i, src in enumerate(src_list[:60]):
                mark = "✓ " if src == cur else ""
                btn = InlineKeyboardButton(
                    text=f"{mark}{src[:24]}",
                    callback_data=f"a:{short}:mp_src:{i}"[:64],
                )
                row.append(btn)
                if len(row) >= 2:
                    rows.append(row); row = []
            if row:
                rows.append(row)
            rows.append([
                InlineKeyboardButton(text="🔙 К карточке", callback_data=f"e:{short}"),
                InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
            ])
            fname = attrs.get("friendly_name", entity_id)
            await update_message(
                cb,
                f"📺 <b>{_h(fname)}</b>\nТекущий: <code>{_h(cur)}</code>\nВыбери источник:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                parse_mode="HTML",
            )
            await cb.answer()
            return

        # Спец-кейс: график истории — рендерим PNG и отправляем как фото
        if action.startswith("graph:"):
            try:
                hours = int(action.split(":", 1)[1])
            except (ValueError, IndexError):
                return await cb.answer("Битый callback графика", show_alert=True)
            await cb.answer(f"📊 Рисую график за {hours}ч…")
            try:
                from .graph import render_sparkline
                points = await ha.history(entity_id, hours=hours)
                st = await ha.get_state(entity_id) or {}
                attrs = st.get("attributes", {})
                fname = attrs.get("friendly_name", entity_id)
                unit = attrs.get("unit_of_measurement", "")
                period = (
                    f"{hours} ч" if hours < 48 else f"{hours // 24} д"
                )
                png = render_sparkline(points, title=fname, unit=unit, period_label=period)
                back_to = id_cache.get("_parent", {}).get(short, "m")
                kb_graph = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="📊 6ч", callback_data=f"a:{short}:graph:6"),
                        InlineKeyboardButton(text="📊 24ч", callback_data=f"a:{short}:graph:24"),
                        InlineKeyboardButton(text="📊 7д", callback_data=f"a:{short}:graph:168"),
                    ],
                    [
                        InlineKeyboardButton(text="🔙 К карточке", callback_data=f"e:{short}"),
                        InlineKeyboardButton(text="🏠 Меню", callback_data="m"),
                    ],
                ])
                photo = BufferedInputFile(png, filename=f"{entity_id}_{hours}h.png")
                from aiogram.types import InputMediaPhoto
                try:
                    if cb.message.photo:
                        await cb.message.edit_media(
                            media=InputMediaPhoto(media=photo, caption=f"📊 {fname} — {period}"),
                            reply_markup=kb_graph,
                        )
                    else:
                        await cb.message.delete()
                        await cb.message.answer_photo(
                            photo, caption=f"📊 {fname} — {period}", reply_markup=kb_graph,
                        )
                except Exception as e:
                    log.warning("graph send/edit failed: %s", e)
                    await cb.message.answer_photo(
                        photo, caption=f"📊 {fname} — {period}", reply_markup=kb_graph,
                    )
            except Exception as e:
                log.warning("graph build failed for %s: %s", entity_id, e)
                await cb.answer(f"⚠ Не удалось построить график: {e}", show_alert=True)
            return

        # Кнопка «Обновить» на фото камеры — тот же общий хелпер
        if action == "snapshot":
            return await _send_camera_snapshot(cb, entity_id, short)

        ok, msg = await execute_action(ha, entity_id, action)
        await cb.answer(msg, show_alert=not ok)
        # принудительно обновляем snapshot и перерисовываем экран entity
        await ha.refresh_snapshot(cache_ttl, force=True)
        st = await ha.get_state(entity_id) or {}
        attrs = st.get("attributes", {})
        fname = attrs.get("friendly_name", entity_id)
        state = st.get("state", "?")
        unit = attrs.get("unit_of_measurement", "")
        if entity_id.startswith("binary_sensor."):
            from .classifiers import binary_state_label
            bs_icon, bs_label = binary_state_label(entity_id, attrs, str(state))
            state_render = f"{bs_icon} {bs_label}"
        else:
            state_render = f"{_h(format_state(state, attrs))}{_h(str(unit))}"
        text = f"<b>{_h(fname)}</b>\n<code>{_h(entity_id)}</code>\nСостояние: <code>{state_render}</code>"
        if attrs.get("preset_mode"):
            text += f"\nРежим: <code>{_h(str(attrs['preset_mode']))}</code>"
        if "brightness" in attrs and attrs["brightness"]:
            pct = round(attrs["brightness"] / 255 * 100)
            text += f"\nЯркость: <code>{pct}%</code>"
        try:
            await update_message(cb, 
                text,
                reply_markup=kb_entity_actions(entity_id, st, short, back_to=id_cache.get("_parent", {}).get(short, "m"), is_favorite=(entity_id in favs)),
                parse_mode="HTML",
            )
        except Exception:
            pass  # message not modified — игнорим

    @dp.callback_query(F.data.startswith("c:"))
    async def cb_confirm(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        _, short, action = cb.data.split(":")
        entity_id = id_cache.get(short)
        if not entity_id:
            return await cb.answer("Сессия устарела", show_alert=True)
        await update_message(cb, 
            f"⚠ Подтвердите действие:\n<b>{_h(action)}</b> для <code>{_h(entity_id)}</code>",
            reply_markup=kb_confirm(entity_id, action, short),
            parse_mode="HTML",
        )
        await cb.answer()

    # Прогрев snapshot при старте — не блокируем если HA недоступен
    log.info("warming up HA snapshot...")
    try:
        await ha.refresh_snapshot(cache_ttl, force=True)
    except Exception as e:
        log.warning("Initial HA snapshot failed: %s — будем повторять при первом запросе", e)

    # Live: callback на state_changed — обновляем cached states локально, чтобы
    # render-функции видели свежий статус сразу, без force-refresh всего snapshot.
    async def _on_state_changed(eid: str, old: dict, new: dict) -> None:
        if new:
            ha.snapshot.states[eid] = new

    live_task = asyncio.create_task(state_changed_loop(
        ha.ws_url, ha_token, live_registry, _on_state_changed,
    ))

    log.info("HAPilot started (live registry active)")
    try:
        await dp.start_polling(bot)
    finally:
        live_task.cancel()
        await ha.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
