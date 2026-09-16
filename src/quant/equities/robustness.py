"""Small parameter grid; a robustness display is evidence, not optimization advice."""
from __future__ import annotations
import pandas as pd
from .engine import daily_portfolio_returns, run_backtest
from .analysis import performance
from .schema import Hypothesis


def sensitivity(data: pd.DataFrame, hypothesis: Hypothesis, thresholds: list[float] | None = None, holds: list[int] | None = None) -> pd.DataFrame:
    thresholds = thresholds or sorted({hypothesis.threshold / 2, hypothesis.threshold, hypothesis.threshold * 1.5})
    holds = holds or sorted({max(1, hypothesis.holding_days // 2), hypothesis.holding_days, hypothesis.holding_days * 2})
    rows = []
    for threshold in thresholds:
        for holding_days in holds:
            tested = Hypothesis.model_validate(
                {**hypothesis.model_dump(), "threshold": threshold, "holding_days": holding_days}
            )
            metrics = performance(daily_portfolio_returns(run_backtest(data, tested)))
            rows.append({"threshold": threshold, "holding_days": holding_days, "sharpe": metrics["sharpe"], "observations": metrics["observations"]})
    return pd.DataFrame(rows)
