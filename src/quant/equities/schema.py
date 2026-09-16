"""Constrained, reviewable specifications for daily U.S. equity experiments."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class Signal(str, Enum):
    reversal = "reversal"
    momentum = "momentum"


class Direction(str, Enum):
    long = "long"
    short = "short"


class Hypothesis(BaseModel):
    """A deliberately small DSL. AI may propose this object, never Python code."""
    name: str = Field(min_length=3, max_length=120)
    tickers: list[str] = Field(min_length=1, max_length=100)
    signal: Signal
    lookback_days: int = Field(ge=1, le=252)
    threshold: float = Field(gt=0, le=1, description="Absolute return threshold, decimal")
    volume_ratio_min: Optional[float] = Field(
        default=None, ge=1, le=100, description="Minimum volume / prior average volume"
    )
    volume_lookback_days: int = Field(default=20, ge=5, le=252)
    holding_days: int = Field(ge=1, le=60)
    direction: Direction = Direction.long
    start_date: date
    end_date: date
    benchmark: str = "SPY"
    transaction_cost_bps: float = Field(default=10, ge=0, le=200)
    top_n: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def dates_and_tickers_are_sane(self) -> "Hypothesis":
        self.tickers = sorted({ticker.upper().strip() for ticker in self.tickers})
        if self.start_date >= self.end_date:
            raise ValueError("start_date must precede end_date")
        if self.top_n > len(self.tickers):
            self.top_n = len(self.tickers)
        return self

    @property
    def summary(self) -> str:
        verb = "fell" if self.signal is Signal.reversal else "rose"
        volume = (
            f", with volume at least {self.volume_ratio_min:g}x its prior "
            f"{self.volume_lookback_days}-day average"
            if self.volume_ratio_min is not None
            else ""
        )
        return (f"{self.direction.value.title()} the {self.top_n} eligible stocks that {verb} "
                f"at least {self.threshold:.1%} over {self.lookback_days} days{volume}, "
                f"hold {self.holding_days} trading days; {self.transaction_cost_bps:g} bps per side.")
