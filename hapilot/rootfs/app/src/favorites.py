"""Избранное / Pinned — entity_ids которые админ закрепил для быстрого доступа.

Хранение: JSON-файл со списком entity_id и порядком (как в add() пришли).
Один общий список на инстанс бота (не per-user — на 1-2 человек хватит).
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class FavoritesStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._items: list[str] = []  # порядок важен — отображаем как закрепили
        self.load()

    def load(self):
        if not self.path.exists():
            log.info("favorites: no store yet at %s", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = list(data.get("items", []))
            log.info("favorites loaded: %d items", len(self._items))
        except Exception as e:
            log.error("favorites load failed: %s", e)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"items": self._items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    @property
    def items(self) -> list[str]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, entity_id: str) -> bool:
        return entity_id in self._items

    def add(self, entity_id: str) -> None:
        if entity_id not in self._items:
            self._items.append(entity_id)
            self.save()

    def remove(self, entity_id: str) -> None:
        if entity_id in self._items:
            self._items.remove(entity_id)
            self.save()

    def toggle(self, entity_id: str) -> bool:
        """Toggle. Возвращает True если теперь избранное, False — если убрано."""
        if entity_id in self._items:
            self.remove(entity_id)
            return False
        self.add(entity_id)
        return True
