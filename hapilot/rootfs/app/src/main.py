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
    Message,
)
from dotenv import load_dotenv

from .classifiers import icon_for_area
from .ha_client import HAClient
from .menus.actions import (
    execute_action,
    kb_confirm,
    kb_entity_actions,
    needs_confirmation,
)
from .menus.alerts import kb_alerts_problems, kb_alerts_root, kb_alerts_unavail
from .menus.domains import kb_domain_entities, kb_domains_root
from .menus.rooms import kb_room_domain, kb_room_domains, kb_rooms_root
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


def kb_main_menu(is_admin: bool, edit_mode: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📍 По комнатам", callback_data="r:")],
        [InlineKeyboardButton(text="📋 По типам", callback_data="d:")],
        [InlineKeyboardButton(text="🚨 Алерты", callback_data="al:")],
    ]
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

    def _main_kb(uid: int) -> InlineKeyboardMarkup:
        return kb_main_menu(is_admin(uid, admin_user_ids), edit_mode.get(uid, False))

    @dp.message(Command("start"))
    async def cmd_start(msg: Message):
        if not is_allowed(msg.from_user.id, allowed_user_ids, admin_user_ids):
            await msg.reply("Доступ запрещён.")
            log.warning("denied access user_id=%s", msg.from_user.id)
            return
        await msg.reply(
            f"🏠 <b>{_h(instance_name)}</b>\nВыберите способ управления:",
            reply_markup=_main_kb(msg.from_user.id),
            parse_mode="HTML",
        )

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
            domain = sub
            title, kb = kb_domain_entities(snap, domain, id_cache, vis)
            await update_message(cb, title, reply_markup=kb, parse_mode="HTML")
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
            await update_message(cb, 
                f"{icon_for_area(area_name)} <b>{_h(area_name)}</b> / {_h(domain)}{suffix}",
                reply_markup=kb_room_domain(snap, area_id, domain, id_cache, vis, em),
                parse_mode="HTML",
            )
        await cb.answer()

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

    @dp.callback_query(F.data.startswith("e:"))
    async def cb_entity(cb: CallbackQuery):
        if not is_allowed(cb.from_user.id, allowed_user_ids, admin_user_ids):
            return await cb.answer("Доступ запрещён", show_alert=True)
        short = cb.data.split(":", 1)[1]
        entity_id = id_cache.get(short)
        if not entity_id:
            return await cb.answer("Сессия устарела, нажмите /start", show_alert=True)
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
            state_render = f"{_h(str(state))}{_h(str(unit))}"
        text = f"<b>{_h(fname)}</b>\n<code>{_h(entity_id)}</code>\nСостояние: <code>{state_render}</code>"
        if attrs.get("preset_mode"):
            text += f"\nРежим: <code>{_h(str(attrs['preset_mode']))}</code>"
        if "brightness" in attrs and attrs["brightness"]:
            pct = round(attrs["brightness"] / 255 * 100)
            text += f"\nЯркость: <code>{pct}%</code>"

        await update_message(cb, 
            text,
            reply_markup=kb_entity_actions(entity_id, st, short, back_to=id_cache.get("_parent", {}).get(short, "m")),
            parse_mode="HTML",
        )
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

        # Спец-кейс: snapshot камеры — заменяем текущее сообщение на фото с теми же кнопками
        if action == "snapshot":
            await cb.answer("📸 Делаю снимок…")
            try:
                jpeg = await ha.camera_snapshot(entity_id)
                if not jpeg:
                    await cb.answer("⚠ Камера не вернула кадр", show_alert=True)
                    return
                # Сохраняем фото на месте текущего сообщения, кнопки рисуем те же
                from aiogram.types import InputMediaPhoto
                back_to = id_cache.get("_parent", {}).get(short, "m")
                kb_photo = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"a:{short}:snapshot")],
                    [InlineKeyboardButton(text="← Назад", callback_data=back_to)],
                ])
                fname = (await ha.get_state(entity_id) or {}).get("attributes", {}).get("friendly_name", entity_id)
                photo = BufferedInputFile(jpeg, filename=f"{entity_id}.jpg")
                try:
                    # Если текущее сообщение текстовое — удаляем и шлём новое фото на его месте
                    # Если уже фото — edit_media заменит кадр
                    if cb.message.photo:
                        await cb.message.edit_media(
                            media=InputMediaPhoto(media=photo, caption=f"📷 {fname}"),
                            reply_markup=kb_photo,
                        )
                    else:
                        await cb.message.delete()
                        await cb.message.answer_photo(
                            photo, caption=f"📷 {fname}", reply_markup=kb_photo,
                        )
                except Exception as e:
                    log.warning("camera snapshot edit failed: %s", e)
                    await cb.message.answer_photo(
                        photo, caption=f"📷 {fname}", reply_markup=kb_photo,
                    )
            except Exception as e:
                log.warning("camera snapshot failed: %s", e)
                await cb.answer(f"⚠ Не удалось получить кадр: {e}", show_alert=True)
            return

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
            state_render = f"{_h(str(state))}{_h(str(unit))}"
        text = f"<b>{_h(fname)}</b>\n<code>{_h(entity_id)}</code>\nСостояние: <code>{state_render}</code>"
        if attrs.get("preset_mode"):
            text += f"\nРежим: <code>{_h(str(attrs['preset_mode']))}</code>"
        if "brightness" in attrs and attrs["brightness"]:
            pct = round(attrs["brightness"] / 255 * 100)
            text += f"\nЯркость: <code>{pct}%</code>"
        try:
            await update_message(cb, 
                text,
                reply_markup=kb_entity_actions(entity_id, st, short, back_to=id_cache.get("_parent", {}).get(short, "m")),
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

    @dp.callback_query(F.data == "back")
    async def cb_back(cb: CallbackQuery):
        await update_message(cb, 
            f"🏠 <b>{_h(instance_name)}</b>\nВыберите способ управления:",
            reply_markup=kb_main_menu(instance_name),
            parse_mode="HTML",
        )
        await cb.answer()

    # Прогрев snapshot при старте — не блокируем если HA недоступен
    log.info("warming up HA snapshot...")
    try:
        await ha.refresh_snapshot(cache_ttl, force=True)
    except Exception as e:
        log.warning("Initial HA snapshot failed: %s — будем повторять при первом запросе", e)

    log.info("HAPilot started")
    try:
        await dp.start_polling(bot)
    finally:
        await ha.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
