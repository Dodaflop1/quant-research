"""Deterministic daily-bar event study. Signals execute on the following close."""
from __future__ import annotations

import numpy as np
import pandas as pd
from .schema import Direction, Hypothesis, Signal


def _selected_signals(data: pd.DataFrame, hypothesis: Hypothesis) -> pd.DataFrame:
    """Select signals using only information available at that day's close."""
    frame = data[data.ticker.isin(hypothesis.tickers)].copy().sort_values(["ticker", "date"])
    frame = frame.sort_values(["ticker", "date"])
    frame["lookback_return"] = frame.groupby("ticker").close.pct_change(hypothesis.lookback_days)
    frame["volume_ratio"] = frame.volume / frame.groupby("ticker").volume.transform(
        lambda values: values.shift(1).rolling(
            hypothesis.volume_lookback_days, min_periods=hypothesis.volume_lookback_days
        ).mean()
    )
    if hypothesis.signal is Signal.reversal:
        eligible = frame[frame.lookback_return <= -hypothesis.threshold]
    else:
        eligible = frame[frame.lookback_return >= hypothesis.threshold]
    if hypothesis.volume_ratio_min is not None:
        eligible = eligible[eligible.volume_ratio >= hypothesis.volume_ratio_min]
    eligible = eligible[(eligible.date.dt.date >= hypothesis.start_date) & (eligible.date.dt.date <= hypothesis.end_date)]
    # Rank cross-sectionally: worst fallers for reversal, strongest movers for momentum.
    ascending = hypothesis.signal is Signal.reversal
    eligible["rank"] = eligible.groupby("date").lookback_return.rank(method="first", ascending=ascending)
    return eligible[eligible["rank"] <= hypothesis.top_n].copy()


def run_backtest(data: pd.DataFrame, hypothesis: Hypothesis) -> pd.DataFrame:
    """Event-level summary retained for audit; use ``portfolio_daily_path`` for equity curves."""
    frame = data[data.ticker.isin(hypothesis.tickers)].copy().sort_values(["ticker", "date"])
    frame["forward_return"] = frame.groupby("ticker").close.shift(-hypothesis.holding_days) / frame.close - 1
    trades = _selected_signals(data, hypothesis).merge(
        frame[["date", "ticker", "forward_return"]], on=["date", "ticker"], how="left"
    ).dropna(subset=["forward_return"])
    gross = trades.forward_return * (1 if hypothesis.direction is Direction.long else -1)
    # Round-trip cost: entry plus exit. Daily close-to-close fills are a transparent approximation.
    trades["net_return"] = gross - 2 * hypothesis.transaction_cost_bps / 10_000
    trades["gross_return"] = gross
    return trades[["date", "ticker", "lookback_return", "forward_return", "gross_return", "net_return"]].reset_index(drop=True)


def portfolio_daily_path(data: pd.DataFrame, hypothesis: Hypothesis) -> pd.DataFrame:
    """Daily portfolio returns with next-day execution and overlapping equal-weight positions.

    A signal observed at a close opens on the following trading day. Entry and exit
    costs are each charged once. This avoids representing a close-derived signal as
    though it could have traded at that same close.
    """
    universe = data[data.ticker.isin(hypothesis.tickers)].copy().sort_values(["ticker", "date"])
    signals = _selected_signals(universe, hypothesis)
    dates = pd.Index(sorted(universe[(universe.date.dt.date >= hypothesis.start_date) & (universe.date.dt.date <= hypothesis.end_date)].date.unique()), name="date")
    daily: dict[pd.Timestamp, list[float]] = {day: [] for day in dates}
    cost = hypothesis.transaction_cost_bps / 10_000
    sign = 1 if hypothesis.direction is Direction.long else -1

    for signal in signals.itertuples():
        ticker = universe[universe.ticker == signal.ticker].reset_index(drop=True)
        signal_rows = ticker.index[ticker.date == signal.date]
        if signal_rows.empty:
            continue
        entry = int(signal_rows[0]) + 1
        exit_index = entry + hypothesis.holding_days
        if exit_index >= len(ticker):
            continue
        for index in range(entry, exit_index + 1):
            day = ticker.at[index, "date"]
            if day not in daily:
                continue
            if index == entry:
                value = -cost
            else:
                value = sign * (ticker.at[index, "close"] / ticker.at[index - 1, "close"] - 1)
                if index == exit_index:
                    value -= cost
            daily[day].append(value)

    path = pd.DataFrame(
        {
            "net_return": [float(np.mean(daily[day])) if daily[day] else 0.0 for day in dates],
            "active_positions": [len(daily[day]) for day in dates],
        },
        index=dates,
    )
    return path


def daily_portfolio_returns(trades: pd.DataFrame) -> pd.Series:
    """Equal-weight signals by signal date; overlapping holdings are intentionally visible."""
    if trades.empty:
        return pd.Series(dtype=float, name="net_return")
    return trades.groupby("date").net_return.mean().rename("net_return")


def equity_curve(returns: pd.Series, initial_investment: float) -> pd.DataFrame:
    """Compound selected-period returns and calculate peak-to-trough drawdown."""
    if initial_investment <= 0:
        raise ValueError("initial_investment must be positive")
    values = returns.dropna().sort_index()
    capital = initial_investment * (1 + values).cumprod()
    peak = capital.cummax()
    return pd.DataFrame(
        {
            "portfolio_value": capital,
            "cumulative_return_pct": (capital / initial_investment - 1) * 100,
            "drawdown": capital / peak - 1,
            "drawdown_pct": (capital / peak - 1) * 100,
        }
    )
