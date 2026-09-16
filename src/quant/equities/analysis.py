"""Metrics and inference separated from execution so results remain auditable."""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats


def performance(returns: pd.Series) -> dict[str, float | int]:
    returns = returns.dropna()
    if returns.empty:
        return {"observations": 0, "total_return": 0.0, "annualized_return": 0.0, "annualized_volatility": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    equity = (1 + returns).cumprod()
    if len(returns) < 2:
        return {"observations": int(len(returns)), "total_return": float(equity.iloc[-1] - 1), "annualized_return": 0.0, "annualized_volatility": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    years = len(returns) / 252
    annualized = equity.iloc[-1] ** (1 / years) - 1 if years else 0.0
    volatility = returns.std(ddof=1) * np.sqrt(252) if len(returns) > 1 else 0.0
    sharpe = (returns.mean() / returns.std(ddof=1) * np.sqrt(252)) if returns.std(ddof=1) else 0.0
    drawdown = equity / equity.cummax() - 1
    return {"observations": int(len(returns)), "total_return": float(equity.iloc[-1] - 1), "annualized_return": float(annualized), "annualized_volatility": float(volatility), "sharpe": float(sharpe), "max_drawdown": float(drawdown.min())}


def statistical_test(returns: pd.Series) -> dict[str, float | int]:
    values = returns.dropna().to_numpy()
    if len(values) < 2 or np.isclose(values.std(ddof=1), 0):
        return {"n": int(len(values)), "mean_return": float(values.mean()) if len(values) else 0.0, "t_statistic": 0.0, "p_value": 1.0}
    tested = stats.ttest_1samp(values, 0.0, alternative="greater")
    return {"n": int(len(values)), "mean_return": float(values.mean()), "t_statistic": float(tested.statistic), "p_value": float(tested.pvalue)}
