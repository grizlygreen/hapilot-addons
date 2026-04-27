"""Per-instance visibility overrides.

Хранение в JSON-файле: список entity_id которые admin вручную скрыл из меню.
По умолчанию используется автофильтр (entity_category=config|diagnostic).
Видимость переопределяет автофильтр: можно вернуть скрытое и скрыть видимое.
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class VisibilityStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        # entity_id'ы которые админ принудительно скрыл
        self._hidden: set[str] = set()
        # entity_id'ы которые админ принудительно показал (оверрайд автофильтра)
        self._forced_visible: set[str] = set()
        self.load()

    def load(self):
        if not self.path.exists():
            log.info("visibility: no store yet at %s", self.path)
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._hidden = set(data.get("hidden", []))
            self._forced_visible = set(data.get("forced_visible", []))
            log.info(
                "visibility loaded: %d hidden, %d forced_visible",
                len(self._hidden),
                len(self._forced_visible),
            )
        except Exception as e:
            log.error("visibility load failed: %s", e)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "hidden": sorted(self._hidden),
                    "forced_visible": sorted(self._forced_visible),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def is_visible(self, entity: dict) -> bool:
        """Стоит ли показывать в обычном режиме."""
        eid = entity["entity_id"]
        if eid in self._forced_visible:
            return True
        if eid in self._hidden:
            return False
        # Автофильтр по умолчанию
        if entity.get("disabled_by") or entity.get("hidden_by"):
            return False
        if entity.get("entity_category") in ("config", "diagnostic"):
            return False
        return True

    def is_explicitly_hidden(self, entity_id: str) -> bool:
        return entity_id in self._hidden

    def is_explicitly_visible(self, entity_id: str) -> bool:
        return entity_id in self._forced_visible

    def toggle(self, entity: dict) -> bool:
        """Переключить видимость. Возвращает новое состояние (True=visible)."""
        eid = entity["entity_id"]
        currently_visible = self.is_visible(entity)
        # Очищаем оба override-set'а перед установкой нового состояния
        self._hidden.discard(eid)
        self._forced_visible.discard(eid)
        if currently_visible:
            # было видно → скрываем
            self._hidden.add(eid)
            new = False
        else:
            # было скрыто → показываем
            self._forced_visible.add(eid)
            new = True
        self.save()
        return new

    @property
    def stats(self) -> tuple[int, int]:
        return len(self._hidden), len(self._forced_visible)
