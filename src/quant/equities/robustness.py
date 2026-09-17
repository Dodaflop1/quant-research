"""Small parameter grid; a robustness display is evidence, not optimization advice."""
from __future__ import annotations
import pandas as pd
from .engine import build_ledger
from .analysis import performance
from .schema import Hypothesis


def sensitivity(
    data: pd.DataFrame,
    hypothesis: Hypothesis,
    thresholds: list[float] | None = None,
    holds: list[int] | None = None,
    initial_investment: float = 10_000.0,
) -> pd.DataFrame:
    thresholds = thresholds or sorted({hypothesis.threshold / 2, hypothesis.threshold, hypothesis.threshold * 1.5})
    holds = holds or sorted({max(1, hypothesis.holding_days // 2), hypothesis.holding_days, hypothesis.holding_days * 2})
    rows = []
    for threshold in thresholds:
        for holding_days in holds:
            tested = Hypothesis.model_validate(
                {**hypothesis.model_dump(), "threshold": threshold, "holding_days": holding_days}
            )
            result = build_ledger(data, tested, initial_investment)
            metrics = performance(result.daily["net_return"])
            rows.append(
                {
                    "threshold": threshold,
                    "holding_days": holding_days,
                    "total_return": metrics["total_return"],
                    "max_drawdown": metrics["max_drawdown"],
                    "completed_trades": len(result.trades),
                    "skipped_signals": len(result.skipped_signals),
                }
            )
    return pd.DataFrame(rows)
