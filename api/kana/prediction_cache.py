"""Bounded reuse of deterministic readings for one loaded model instance."""
from functools import lru_cache


class CachedPredictor:
    def __init__(self, predict, maxsize: int = 256):
        self._predict = lru_cache(maxsize=maxsize)(predict)

    def __call__(self, name: str) -> list[dict]:
        if not name:
            return []
        # Keep callers from modifying another request's cached candidates.
        return [dict(candidate) for candidate in self._predict(name)]
