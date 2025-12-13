import sys
from typing import Any

from cachetools import LRUCache


class MemoryLRUCache(LRUCache):
    def __init__(self, max_memory: int, maxsize: int, *args, **kwargs):
        super().__init__(maxsize=maxsize, *args, **kwargs)
        self.max_memory = max_memory
        self.current_memory = 0

    def _get_size(self, value: Any) -> int:
        if value is None:
            return sys.getsizeof(None)
        if isinstance(value, (int, float, str, bool, bytes)):
            return sys.getsizeof(value)
        elif isinstance(value, (list, tuple, set)):
            return sum(self._get_size(item) for item in value)
        elif isinstance(value, dict):
            return sum(self._get_size(k) + self._get_size(v) for k, v in value.items())
        else:
            try:
                return sys.getsizeof(value)
            except Exception:
                return 0

    def __setitem__(self, key: Any, value: Any) -> None:
        if key in self:
            old_value = self[key]
            self.current_memory -= self._get_size(old_value)

        new_size = self._get_size(value)
        self.current_memory += new_size

        while self.current_memory > self.max_memory and self:
            self.popitem()

        super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        if key in self:
            old_value = self[key]
            self.current_memory -= self._get_size(old_value)

        super().__delitem__(key)
