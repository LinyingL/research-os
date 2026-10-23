"""A bounded map kept in memory between requests (DESIGN.md 18.4, item 4).

The team paths remember what they worked out about files that never change
once written -- a published manifest, a signature -- so that a page that asks
again does not parse, hash or verify it again. Every such memory is a `Memo`:
least recently used goes first, it holds at most `items` entries and, when a
`weight` is given, at most that much in total (a manifest weighs its file
count), and it is safe to use from the server's threads. An entry heavier
than the whole budget is not kept at all. A `Memo` is only ever asked about a
key that holds everything the answer depends on (a content hash, a file's
path, size, modification time and inode), so a stale answer is not possible;
losing an entry costs time, never correctness.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Hashable


class Memo:
    def __init__(self, items: int, weight: int | None = None) -> None:
        self._items, self._budget = items, weight
        self._held: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._total = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            got = self._held.get(key)
            if got is None:
                return default
            self._held.move_to_end(key)
            return got[0]

    def put(self, key: Hashable, value: Any, weight: int = 1) -> None:
        with self._lock:
            old = self._held.pop(key, None)
            if old is not None:
                self._total -= old[1]
            if self._budget is not None and weight > self._budget:
                return
            self._held[key] = (value, weight)
            self._total += weight
            while len(self._held) > self._items or (self._budget is not None and self._total > self._budget):
                _key, (_value, dropped) = self._held.popitem(last=False)
                self._total -= dropped

    def clear(self) -> None:
        with self._lock:
            self._held.clear()
            self._total = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._held)
