"""Home Assistant client — REST + WebSocket для discovery и команд."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import websockets

log = logging.getLogger(__name__)


@dataclass
class HASnapshot:
    """Cached registry + states snapshot."""

    areas: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    states: dict[str, dict] = field(default_factory=dict)
    fetched_at: float = 0.0

    def is_fresh(self, ttl: int) -> bool:
        return (time.time() - self.fetched_at) < ttl

    def entities_in_area(self, area_id: str) -> list[dict]:
        # entity unsiced area_id ИЛИ device.area_id (entity без явной area наследует от device)
        device_areas = {d["id"]: d.get("area_id") for d in self.devices}
        result = []
        for e in self.entities:
            ea = e.get("area_id") or device_areas.get(e.get("device_id"))
            if ea == area_id:
                result.append(e)
        return result

    def entities_by_domain(self, domain: str) -> list[dict]:
        return [e for e in self.entities if e["entity_id"].startswith(domain + ".")]

    def state(self, entity_id: str) -> dict | None:
        return self.states.get(entity_id)

    def is_exposed(self, entity_id: str) -> bool:
        for e in self.entities:
            if e["entity_id"] == entity_id:
                return e.get("options", {}).get("conversation", {}).get("should_expose", False)
        return False


class HAClient:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self.ws_url = self.url.replace("http", "ws", 1) + "/api/websocket"
        self._session: aiohttp.ClientSession | None = None
        self._snapshot = HASnapshot()
        self._snapshot_lock = asyncio.Lock()

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.token}"}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------- WS ----------

    async def _ws_request(self, msgs: list[dict]) -> list[dict]:
        """Отправить пачку команд через одно WS-соединение, вернуть результаты по id."""
        results: dict[int, dict] = {}
        async with websockets.connect(self.ws_url, max_size=10 * 1024 * 1024) as ws:
            await ws.recv()  # auth_required
            await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(await ws.recv())
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"HA auth failed: {auth}")
            for i, m in enumerate(msgs, start=1):
                m["id"] = i
                await ws.send(json.dumps(m))
            need = len(msgs)
            while need > 0:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "result":
                    results[msg["id"]] = msg
                    need -= 1
        return [results[i] for i in range(1, len(msgs) + 1)]

    # ---------- Discovery ----------

    async def refresh_snapshot(self, ttl: int = 60, force: bool = False) -> HASnapshot:
        async with self._snapshot_lock:
            if not force and self._snapshot.is_fresh(ttl):
                return self._snapshot

            log.info("Refreshing HA snapshot")
            try:
                results = await asyncio.wait_for(self._ws_request([
                    {"type": "config/area_registry/list"},
                    {"type": "config/device_registry/list"},
                    {"type": "config/entity_registry/list"},
                ]), timeout=8)

                sess = await self.session()
                async with sess.get(f"{self.url}/api/states", timeout=aiohttp.ClientTimeout(total=8)) as r:
                    r.raise_for_status()
                    states_list = await r.json()
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
                # Fallback: оставляем старый snapshot если был, иначе пустой
                log.warning("HA snapshot refresh failed: %s — using stale data (age %.0fs)",
                            e, time.time() - self._snapshot.fetched_at if self._snapshot.fetched_at else 0)
                if self._snapshot.fetched_at == 0:
                    raise
                return self._snapshot

            self._snapshot = HASnapshot(
                areas=results[0].get("result", []),
                devices=results[1].get("result", []),
                entities=results[2].get("result", []),
                states={s["entity_id"]: s for s in states_list},
                fetched_at=time.time(),
            )
            log.info(
                "Snapshot: %d areas, %d devices, %d entities, %d states",
                len(self._snapshot.areas),
                len(self._snapshot.devices),
                len(self._snapshot.entities),
                len(self._snapshot.states),
            )
            return self._snapshot

    @property
    def snapshot(self) -> HASnapshot:
        return self._snapshot

    # ---------- Actions ----------

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict | None = None,
    ) -> Any:
        """Call a HA service. e.g. call_service('light','turn_on','light.kitchen')"""
        sess = await self.session()
        body: dict[str, Any] = data.copy() if data else {}
        if entity_id:
            body["entity_id"] = entity_id
        async with sess.post(
            f"{self.url}/api/services/{domain}/{service}", json=body
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def get_state(self, entity_id: str) -> dict | None:
        sess = await self.session()
        async with sess.get(f"{self.url}/api/states/{entity_id}") as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            return await r.json()

    async def camera_snapshot(self, entity_id: str) -> bytes | None:
        """Получить текущий кадр камеры как JPEG bytes."""
        sess = await self.session()
        async with sess.get(f"{self.url}/api/camera_proxy/{entity_id}") as r:
            if r.status != 200:
                return None
            return await r.read()

    async def history(
        self, entity_id: str, hours: int = 24
    ) -> list[tuple[float, float]]:
        """Получить историю numeric-сенсора за последние N часов.

        Возвращает список (timestamp_unix, value) отсортированный по времени.
        Невалидные/нечисловые точки пропускаются.
        """
        from datetime import datetime, timedelta, timezone
        end_t = datetime.now(timezone.utc)
        start_t = end_t - timedelta(hours=hours)
        # HA history endpoint: /api/history/period/<start>?filter_entity_id=&end_time=
        start_iso = start_t.strftime("%Y-%m-%dT%H:%M:%S")
        end_iso = end_t.strftime("%Y-%m-%dT%H:%M:%S")
        url = f"{self.url}/api/history/period/{start_iso}"
        params = {
            "filter_entity_id": entity_id,
            "end_time": end_iso,
            "minimal_response": "true",
            "no_attributes": "true",
        }
        sess = await self.session()
        async with sess.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            data = await r.json()
        # data — list of lists. Берём первый список (наш entity).
        if not data or not data[0]:
            return []
        result: list[tuple[float, float]] = []
        for point in data[0]:
            try:
                ts = datetime.fromisoformat(
                    point["last_changed"].replace("Z", "+00:00")
                ).timestamp()
                v = float(point["state"])
                result.append((ts, v))
            except (KeyError, ValueError, TypeError):
                continue
        result.sort(key=lambda p: p[0])
        return result
