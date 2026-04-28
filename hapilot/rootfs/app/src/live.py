"""Live-обновление открытых экранов через HA WebSocket state_changed.

ScreenRegistry хранит «какое сообщение какого чата сейчас показывает какие entities».
Background task держит WS-соединение к HA, на state_changed дёргает rerender для
всех экранов где упомянут изменившийся entity_id.

Throttling: не чаще 1 edit/сек на экран (чтобы уложиться в 1 edit/сек/чат лимит TG).
TTL: экран автоудаляется через LIVE_SCREEN_TTL после последнего render.
Caps: не более LIVE_MAX_SCREENS экранов одновременно (вытесняем самые старые).
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets

log = logging.getLogger(__name__)

LIVE_SCREEN_TTL = 600.0  # 10 мин
LIVE_MAX_SCREENS = 50
LIVE_DEBOUNCE = 1.0  # 1 сек throttle на экран


@dataclass
class Screen:
    """Зарегистрированный открытый экран с inline-клавиатурой."""

    chat_id: int
    message_id: int
    entity_ids: set[str]
    render: Callable[[], Awaitable[None]]  # async() — пересобрать и edit_message
    registered_at: float = field(default_factory=time.time)
    last_render_at: float = field(default_factory=time.time)
    pending: bool = False  # true если уже запланирован debounced rerender

    @property
    def key(self) -> tuple[int, int]:
        return (self.chat_id, self.message_id)


class ScreenRegistry:
    def __init__(self):
        self._screens: dict[tuple[int, int], Screen] = {}

    def register(self, screen: Screen) -> None:
        # GC: убираем экран этого же чата с любым другим message_id
        # (TG-юзер обычно edit'ит то же сообщение, но при отправке нового мы хотим забыть старое)
        for k in list(self._screens):
            if k[0] == screen.chat_id and k != screen.key:
                self._screens.pop(k, None)
        self._screens[screen.key] = screen
        # Cap: не больше N экранов
        if len(self._screens) > LIVE_MAX_SCREENS:
            oldest = min(self._screens.values(), key=lambda s: s.registered_at)
            self._screens.pop(oldest.key, None)

    def unregister(self, chat_id: int, message_id: int) -> None:
        self._screens.pop((chat_id, message_id), None)

    def find_for_entity(self, entity_id: str) -> list[Screen]:
        now = time.time()
        result = []
        # GC по TTL
        stale = [s.key for s in self._screens.values() if now - s.last_render_at > LIVE_SCREEN_TTL]
        for k in stale:
            self._screens.pop(k, None)
        for s in self._screens.values():
            if entity_id in s.entity_ids:
                result.append(s)
        return result

    def __len__(self) -> int:
        return len(self._screens)


async def state_changed_loop(
    ws_url: str,
    token: str,
    registry: ScreenRegistry,
    on_state_changed: Callable[[str, dict, dict], Awaitable[None]] | None = None,
) -> None:
    """Подключиться к HA WS, подписаться на state_changed, дёргать render на каждом event.

    Бесконечный loop с reconnect. on_state_changed (если задан) вызывается ДО render
    (например, чтобы обновить cached snapshot).
    """
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
                await ws.recv()  # auth_required
                await ws.send(json.dumps({"type": "auth", "access_token": token}))
                auth = json.loads(await ws.recv())
                if auth.get("type") != "auth_ok":
                    raise RuntimeError(f"HA WS auth failed: {auth}")

                await ws.send(json.dumps({
                    "id": 1,
                    "type": "subscribe_events",
                    "event_type": "state_changed",
                }))
                ack = json.loads(await ws.recv())
                if not ack.get("success"):
                    raise RuntimeError(f"subscribe_events failed: {ack}")

                log.info("Live WS subscribed to state_changed")
                backoff = 1.0  # reset

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "event":
                        continue
                    data = msg.get("event", {}).get("data", {})
                    eid = data.get("entity_id")
                    if not eid:
                        continue
                    new_state = data.get("new_state") or {}
                    old_state = data.get("old_state") or {}

                    # Обновляем cached snapshot если задан callback
                    if on_state_changed:
                        try:
                            await on_state_changed(eid, old_state, new_state)
                        except Exception as e:
                            log.warning("on_state_changed callback failed: %s", e)

                    # Находим экраны и планируем rerender
                    screens = registry.find_for_entity(eid)
                    for s in screens:
                        await _schedule_rerender(s)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:
            log.warning("Live WS loop error: %s — reconnect in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def _schedule_rerender(screen: Screen) -> None:
    """Дебаунсер: если уже pending — игнорируем. Иначе — запускаем delayed rerender."""
    if screen.pending:
        return
    screen.pending = True

    async def _do():
        # ждём debounce окно — может ещё прилетят события для этого экрана
        delay = max(0.0, LIVE_DEBOUNCE - (time.time() - screen.last_render_at))
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await screen.render()
            screen.last_render_at = time.time()
        except Exception as e:
            log.warning("live rerender failed for screen=%s: %s", screen.key, e)
        finally:
            screen.pending = False

    asyncio.create_task(_do())
