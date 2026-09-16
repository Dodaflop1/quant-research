from datetime import date
import numpy as np
import pandas as pd
import pytest
from quant.equities.analysis import performance, statistical_test
from quant.equities.engine import (
    _selected_signals,
    daily_portfolio_returns,
    equity_curve,
    portfolio_daily_path,
    run_backtest,
)
from quant.equities.robustness import sensitivity
from quant.equities.schema import Hypothesis


def _data() -> pd.DataFrame:
    dates = pd.date_range("2023-01-02", periods=12, freq="B")
    prices = [100, 100, 100, 100, 90, 92, 95, 96, 97, 98, 99, 100]
    return pd.DataFrame({"date": dates, "ticker": "TEST", "close": prices, "volume": 1000})


def _hypothesis(**changes) -> Hypothesis:
    fields = dict(name="Reversal test", tickers=["TEST"], signal="reversal", lookback_days=1, threshold=.05, holding_days=2, direction="long", start_date=date(2023, 1, 2), end_date=date(2023, 1, 17), top_n=1, transaction_cost_bps=10)
    fields.update(changes)
    return Hypothesis(**fields)


def test_reversal_executes_deterministically_and_charges_round_trip_cost():
    trades = run_backtest(_data(), _hypothesis())
    assert len(trades) == 1
    assert trades.iloc[0].gross_return == pytest.approx(95 / 90 - 1)
    assert trades.iloc[0].net_return == pytest.approx(95 / 90 - 1 - .002)


def test_schema_rejects_inverted_dates():
    with pytest.raises(ValueError, match="precede"):
        _hypothesis(start_date=date(2023, 2, 1), end_date=date(2023, 1, 1))


def test_analysis_handles_return_series_and_sensitivity():
    returns = daily_portfolio_returns(run_backtest(_data(), _hypothesis()))
    assert performance(returns)["observations"] == 1
    assert statistical_test(returns)["p_value"] == 1.0
    grid = sensitivity(_data(), _hypothesis())
    assert len(grid) == 9
    assert set(grid.columns) == {"threshold", "holding_days", "sharpe", "observations"}
    assert statistical_test(pd.Series([0.0, 0.0])) == {
        "n": 2,
        "mean_return": 0.0,
        "t_statistic": 0.0,
        "p_value": 1.0,
    }


def test_equity_curve_compounds_and_measures_drawdown():
    curve = equity_curve(pd.Series([0.10, -0.20, 0.05]), 1_000)
    assert curve.iloc[-1].portfolio_value == pytest.approx(924)
    assert curve.iloc[-1].cumulative_return_pct == pytest.approx(-7.6)
    assert curve.iloc[1].drawdown == pytest.approx(-0.20)
    assert curve.iloc[1].drawdown_pct == pytest.approx(-20)


def test_daily_path_executes_after_signal_and_is_flat_without_positions():
    path = portfolio_daily_path(_data(), _hypothesis())
    signal_day = pd.Timestamp("2023-01-06")  # the 10% drop was observed on Jan. 6
    assert path.loc[signal_day, "net_return"] == 0.0
    assert path.loc[pd.Timestamp("2023-01-09"), "active_positions"] == 1
    assert path.loc[pd.Timestamp("2023-01-09"), "net_return"] == pytest.approx(-0.001)


def test_volume_filter_requires_volume_above_prior_average():
    dates = pd.date_range("2023-01-02", periods=32, freq="B")
    prices = [100.0] * 25 + [90.0] + [91.0] * 6
    volumes = [1_000] * 25 + [3_000] + [1_000] * 6
    data = pd.DataFrame({"date": dates, "ticker": "TEST", "close": prices, "volume": volumes})
    signals = _selected_signals(
        data,
        _hypothesis(
            volume_ratio_min=2.0,
            volume_lookback_days=20,
            start_date=date(2023, 1, 2),
            end_date=date(2023, 3, 1),
        ),
    )
    assert len(signals) == 1
    assert signals.iloc[0].volume_ratio == pytest.approx(3.0)
