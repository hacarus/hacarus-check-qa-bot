"""トークン数から API の従量課金だった場合の料金を計算する"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PER_MILLION = 1_000_000


@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input + other.input,
            self.output + other.output,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
        )

    def __sub__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input - other.input,
            self.output - other.output,
            self.cache_write - other.cache_write,
            self.cache_read - other.cache_read,
        )

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


@dataclass(frozen=True)
class ModelPrice:
    """1M トークンあたりの USD"""

    input: float
    output: float
    cache_write: float
    cache_read: float

    def cost(self, usage: TokenUsage) -> float:
        return (
            usage.input * self.input
            + usage.output * self.output
            + usage.cache_write * self.cache_write
            + usage.cache_read * self.cache_read
        ) / PER_MILLION


class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice], as_of: str = ""):
        self.prices = prices
        self.as_of = as_of

    @classmethod
    def load(cls, path: Path) -> "PriceTable":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        prices = {
            name: ModelPrice(
                input=float(v["input"]),
                output=float(v["output"]),
                cache_write=float(v["cache_write"]),
                cache_read=float(v["cache_read"]),
            )
            for name, v in data.get("models", {}).items()
        }
        return cls(prices, str(data.get("as_of", "")))

    def find(self, model: str) -> ModelPrice | None:
        """`claude-haiku-4-5-20251001` のような日付付き ID も前方一致で引く"""
        if model in self.prices:
            return self.prices[model]
        candidates = [k for k in self.prices if model.startswith(k)]
        if not candidates:
            return None
        return self.prices[max(candidates, key=len)]

    def cost(self, model: str, usage: TokenUsage) -> float | None:
        price = self.find(model)
        return None if price is None else price.cost(usage)
