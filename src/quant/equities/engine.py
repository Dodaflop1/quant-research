"""Deterministic daily-close portfolio ledger for U.S. equity research."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schema import Hypothesis, Signal


@dataclass(frozen=True)
class BacktestResult:
    """One reconciled experiment. Every displayed result derives from this ledger."""

    daily: pd.DataFrame
    trades: pd.DataFrame
    skipped_signals: pd.DataFrame


def _selected_signals(data: pd.DataFrame, hypothesis: Hypothesis) -> pd.DataFrame:
    """Select close-known signals, retaining earlier rows only for feature warm-up."""
    frame = data[data.ticker.isin(hypothesis.tickers)].copy().sort_values(["ticker", "date"])
    frame["lookback_return"] = frame.groupby("ticker").close.pct_change(hypothesis.lookback_days)
    frame["volume_ratio"] = frame.volume / frame.groupby("ticker").volume.transform(
        lambda values: values.shift(1).rolling(
            hypothesis.volume_lookback_days, min_periods=hypothesis.volume_lookback_days
        ).mean()
    )
    if hypothesis.signal is Signal.reversal:
        eligible = frame[frame.lookback_return <= -hypothesis.threshold]
        ascending = True
    else:
        eligible = frame[frame.lookback_return >= hypothesis.threshold]
        ascending = False
    if hypothesis.volume_ratio_min is not None:
        eligible = eligible[eligible.volume_ratio >= hypothesis.volume_ratio_min]
    eligible = eligible[(eligible.date.dt.date >= hypothesis.start_date) & (eligible.date.dt.date <= hypothesis.end_date)].copy()
    eligible["rank"] = eligible.groupby("date").lookback_return.rank(method="first", ascending=ascending)
    return eligible[eligible["rank"] <= hypothesis.top_n].sort_values(["date", "rank", "ticker"]).reset_index(drop=True)


def _study_dates(data: pd.DataFrame, hypothesis: Hypothesis) -> pd.DatetimeIndex:
    dates = data.loc[
        data.ticker.isin(hypothesis.tickers)
        & (data.date.dt.date >= hypothesis.start_date)
        & (data.date.dt.date <= hypothesis.end_date),
        "date",
    ].drop_duplicates().sort_values()
    return pd.DatetimeIndex(dates, name="date")


def _signal_schedule(data: pd.DataFrame, hypothesis: Hypothesis) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Map signals to ticker-specific next-close entries and completed exits."""
    signals = _selected_signals(data, hypothesis)
    scheduled: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    end = pd.Timestamp(hypothesis.end_date)
    for signal in signals.itertuples(index=False):
        ticker = data[data.ticker == signal.ticker].sort_values("date").reset_index(drop=True)
        row = ticker.index[ticker.date == signal.date]
        entry_index = int(row[0]) + 1 if len(row) else len(ticker)
        exit_index = entry_index + hypothesis.holding_days
        if entry_index >= len(ticker) or exit_index >= len(ticker):
            skipped.append({"signal_date": signal.date, "ticker": signal.ticker, "reason": "insufficient_future_prices"})
            continue
        entry_date = ticker.at[entry_index, "date"]
        exit_date = ticker.at[exit_index, "date"]
        if entry_date > end or exit_date > end:
            skipped.append({"signal_date": signal.date, "ticker": signal.ticker, "reason": "holding_extends_beyond_period"})
            continue
        scheduled.append(
            {
                "signal_date": signal.date, "entry_date": entry_date, "exit_date": exit_date,
                "ticker": signal.ticker, "rank": int(signal.rank),
                "lookback_return": float(signal.lookback_return),
            }
        )
    return pd.DataFrame(scheduled), pd.DataFrame(skipped)


def build_ledger(data: pd.DataFrame, hypothesis: Hypothesis, initial_investment: float = 10_000.0) -> BacktestResult:
    """Execute a completed-trades-only, unlevered daily-close portfolio.

    A signal known at close *t* buys at the next available close and sells after
    ``holding_days`` further closes. Each position is capped at initial capital /
    top_n, fees come from cash, and the portfolio never borrows or doubles a ticker.
    """
    if initial_investment <= 0:
        raise ValueError("initial_investment must be positive")
    universe = data[data.ticker.isin(hypothesis.tickers)].copy().sort_values(["ticker", "date"])
    dates = _study_dates(universe, hypothesis)
    schedule, skipped = _signal_schedule(universe, hypothesis)
    trade_columns = [
        "signal_date", "entry_date", "exit_date", "ticker", "lookback_return", "entry_price", "exit_price",
        "shares", "entry_fee", "exit_fee", "gross_return", "net_return", "net_pnl",
    ]
    if dates.empty:
        return BacktestResult(
            pd.DataFrame(columns=["net_return", "cash", "holdings_value", "portfolio_value", "active_positions"]),
            pd.DataFrame(columns=trade_columns), skipped,
        )

    prices = universe.set_index(["date", "ticker"])["close"]
    entries = {day: rows for day, rows in schedule.groupby("entry_date")} if not schedule.empty else {}
    exits = {day: rows for day, rows in schedule.groupby("exit_date")} if not schedule.empty else {}
    fee_rate = hypothesis.transaction_cost_bps / 10_000
    slot_budget = initial_investment / hypothesis.top_n
    cash = float(initial_investment)
    open_positions: dict[str, dict[str, object]] = {}
    completed: list[dict[str, object]] = []
    daily_rows: list[dict[str, object]] = []
    previous_value = initial_investment

    for day in dates:
        for exit_row in exits.get(day, pd.DataFrame()).itertuples(index=False):
            position = open_positions.pop(exit_row.ticker, None)
            if position is None:
                continue
            exit_price = float(prices.loc[(day, exit_row.ticker)])
            proceeds = float(position["shares"]) * exit_price
            exit_fee = proceeds * fee_rate
            cash += proceeds - exit_fee
            entry_total = float(position["entry_price"]) * float(position["shares"]) + float(position["entry_fee"])
            completed.append(
                {
                    "signal_date": position["signal_date"], "entry_date": position["entry_date"], "exit_date": day,
                    "ticker": exit_row.ticker, "lookback_return": position["lookback_return"],
                    "entry_price": position["entry_price"], "exit_price": exit_price, "shares": position["shares"],
                    "entry_fee": position["entry_fee"], "exit_fee": exit_fee,
                    "gross_return": exit_price / float(position["entry_price"]) - 1,
                    "net_return": (proceeds - exit_fee) / entry_total - 1,
                    "net_pnl": proceeds - exit_fee - entry_total,
                }
            )

        todays_entries = entries.get(day, pd.DataFrame())
        entry_rows = todays_entries.sort_values(["rank", "ticker"]).itertuples(index=False) if not todays_entries.empty else []
        for entry_row in entry_rows:
            if entry_row.ticker in open_positions or len(open_positions) >= hypothesis.top_n:
                continue
            entry_price = float(prices.loc[(day, entry_row.ticker)])
            spend_limit = min(slot_budget, cash)
            shares = spend_limit / (entry_price * (1 + fee_rate))
            if shares <= 0:
                continue
            entry_fee = shares * entry_price * fee_rate
            cash -= shares * entry_price + entry_fee
            open_positions[entry_row.ticker] = {
                "signal_date": entry_row.signal_date, "entry_date": day, "lookback_return": entry_row.lookback_return,
                "entry_price": entry_price, "shares": shares, "entry_fee": entry_fee,
            }

        holdings_value = sum(float(position["shares"]) * float(prices.loc[(day, ticker)]) for ticker, position in open_positions.items())
        value = cash + holdings_value
        daily_rows.append(
            {"date": day, "net_return": value / previous_value - 1, "cash": cash, "holdings_value": holdings_value,
             "portfolio_value": value, "active_positions": len(open_positions)}
        )
        previous_value = value

    daily = pd.DataFrame(daily_rows).set_index("date")
    return BacktestResult(daily, pd.DataFrame(completed, columns=trade_columns), skipped)


def run_backtest(data: pd.DataFrame, hypothesis: Hypothesis, initial_investment: float = 10_000.0) -> pd.DataFrame:
    """Return completed trades derived from the same ledger as portfolio charts."""
    return build_ledger(data, hypothesis, initial_investment).trades


def portfolio_daily_path(data: pd.DataFrame, hypothesis: Hypothesis, initial_investment: float = 10_000.0) -> pd.DataFrame:
    """Return the reconciled daily portfolio path."""
    return build_ledger(data, hypothesis, initial_investment).daily


def daily_portfolio_returns(trades: pd.DataFrame) -> pd.Series:
    """Deprecated event aggregation retained only for backwards-compatible callers."""
    if trades.empty:
        return pd.Series(dtype=float, name="net_return")
    return trades.groupby("exit_date").net_return.mean().rename("net_return")


def equity_curve(returns: pd.Series, initial_investment: float) -> pd.DataFrame:
    """Compound returns and measure drawdown against starting capital and later peaks."""
    if initial_investment <= 0:
        raise ValueError("initial_investment must be positive")
    values = returns.dropna().sort_index()
    capital = initial_investment * (1 + values).cumprod()
    peak = capital.cummax().clip(lower=initial_investment)
    return pd.DataFrame(
        {
            "portfolio_value": capital,
            "cumulative_return_pct": (capital / initial_investment - 1) * 100,
            "drawdown": capital / peak - 1,
            "drawdown_pct": (capital / peak - 1) * 100,
        }
    )
