"""In-process event bus. The interface mirrors a Kafka topic model so a real
producer/consumer can replace it without touching callers."""

from __future__ import annotations

import itertools
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    id: int
    topic: str
    ts: float
    key: str | None
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._subs: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self._history: list[Event] = []
        self._lock = threading.Lock()

    def publish(self, topic: str, payload: dict[str, Any], key: str | None = None) -> Event:
        with self._lock:
            event = Event(next(self._ids), topic, time.time(), key, dict(payload))
            self._history.append(event)
            handlers = list(self._subs.get(topic, [])) + list(self._subs.get("*", []))
        for handler in handlers:
            handler(event)
        return event

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs[topic].append(handler)

    def events(self, topic: str | None = None, key: str | None = None) -> list[Event]:
        with self._lock:
            return [
                e
                for e in self._history
                if (topic is None or e.topic == topic) and (key is None or e.key == key)
            ]
