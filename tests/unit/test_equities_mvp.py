from datetime import date
import io
import zipfile
import numpy as np
import pandas as pd
import pytest
from quant.equities.analysis import performance, statistical_test
from quant.equities.data import coverage_report, load_frame
from quant.equities.engine import (
    _selected_signals,
    benchmark_curve,
    build_ledger,
    daily_portfolio_returns,
    equity_curve,
    portfolio_daily_path,
    run_backtest,
)
from quant.equities.robustness import sensitivity
from quant.equities.schema import Hypothesis
from quant.equities.runs import RunStore


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
    trade = trades.iloc[0]
    assert trade.entry_date == pd.Timestamp("2023-01-09")
    assert trade.exit_date == pd.Timestamp("2023-01-11")
    assert trade.gross_return == pytest.approx(96 / 92 - 1)
    assert trade.net_return == pytest.approx((96 * .999) / (92 * 1.001) - 1)


def test_schema_rejects_inverted_dates():
    with pytest.raises(ValueError, match="precede"):
        _hypothesis(start_date=date(2023, 2, 1), end_date=date(2023, 1, 1))


def test_analysis_handles_return_series_and_sensitivity():
    returns = daily_portfolio_returns(run_backtest(_data(), _hypothesis()))
    assert performance(returns)["observations"] == 1
    assert statistical_test(returns)["p_value"] == 1.0
    grid = sensitivity(_data(), _hypothesis())
    assert len(grid) == 9
    assert set(grid.columns) == {"threshold", "holding_days", "total_return", "max_drawdown", "completed_trades", "skipped_signals"}
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
    assert path.loc[pd.Timestamp("2023-01-09"), "net_return"] == pytest.approx(-.001 / 1.001)


def test_ledger_reconciles_cash_holdings_and_excludes_incomplete_positions():
    result = build_ledger(_data(), _hypothesis(), initial_investment=1_000)
    assert (result.daily.cash + result.daily.holdings_value).equals(result.daily.portfolio_value)
    assert result.daily.iloc[-1].portfolio_value == pytest.approx(1_000 + result.trades.iloc[0].net_pnl)
    shortened = _hypothesis(end_date=date(2023, 1, 10))
    incomplete = build_ledger(_data(), shortened)
    assert incomplete.trades.empty
    assert "holding_extends_beyond_period" in set(incomplete.skipped_signals.reason)


def test_first_day_loss_is_a_drawdown_from_starting_capital():
    assert performance(pd.Series([-.01, .01]))["max_drawdown"] == pytest.approx(-.01)


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


def test_consecutive_down_condition_is_not_a_three_day_cumulative_return_proxy():
    dates = pd.date_range("2023-01-02", periods=8, freq="B")
    data = pd.DataFrame({"date": dates, "ticker": "SPY", "close": [100, 99, 98, 97, 98, 99, 100, 101], "volume": 1_000})
    signals = _selected_signals(data, _hypothesis(tickers=["SPY"], consecutive_down_days=3, threshold=.50))
    assert list(signals.date) == [pd.Timestamp("2023-01-05")]


def test_coverage_and_benchmark_curve_are_aligned_to_portfolio_dates():
    dates = pd.date_range("2023-01-02", periods=4, freq="B")
    data = pd.DataFrame(
        {"date": list(dates) * 2, "ticker": ["TEST"] * 4 + ["SPY"] * 4,
         "close": [100, 101, 102, 103, 200, 202, 204, 206], "volume": 1_000}
    )
    coverage = coverage_report(load_frame(data), ["TEST", "SPY", "MISSING"], dates[0], dates[-1])
    assert dict(zip(coverage.ticker, coverage.observations)) == {"MISSING": 0, "SPY": 4, "TEST": 4}
    curve = benchmark_curve(load_frame(data), "SPY", dates, 1_000)
    assert curve.iloc[0].benchmark_value == pytest.approx(1_000)
    assert curve.iloc[-1].benchmark_return_pct == pytest.approx(3.0)


def test_data_validation_rejects_missing_or_non_finite_prices():
    with pytest.raises(ValueError, match="finite"):
        load_frame(pd.DataFrame({"date": ["2023-01-02"], "ticker": ["TEST"], "close": [np.inf], "volume": [1_000]}))


def test_saved_run_reopens_identical_snapshot_and_exports_bundle(tmp_path):
    data = _data()
    result = build_ledger(data, _hypothesis(), 1_000)
    store = RunStore(tmp_path / "runs")
    saved = store.save(_hypothesis(), 1_000, "Unit test", None, data, result)
    reopened = store.load(saved.run_id)
    assert reopened.data_hash == saved.data_hash
    pd.testing.assert_frame_equal(reopened.data, data)
    pd.testing.assert_frame_equal(reopened.result.daily, result.daily)
    assert reopened.result.trades.iloc[0].net_pnl == pytest.approx(result.trades.iloc[0].net_pnl)
    with zipfile.ZipFile(io.BytesIO(store.export_bundle(saved.run_id))) as bundle:
        assert {"metadata.json", "prices.csv", "daily_ledger.csv", "trades.csv", "skipped_signals.csv"} <= set(bundle.namelist())


def test_saved_run_comparison_identifies_changed_rule_and_parent(tmp_path):
    data = _data()
    store = RunStore(tmp_path / "runs")
    first = store.save(_hypothesis(), 1_000, "Unit test", None, data, build_ledger(data, _hypothesis(), 1_000))
    revised_hypothesis = _hypothesis(holding_days=3)
    second = store.save(
        revised_hypothesis, 1_000, "Saved snapshot", None, data,
        build_ledger(data, revised_hypothesis, 1_000), parent_run_id=first.run_id,
    )
    assert store.load(second.run_id).parent_run_id == first.run_id
    comparison = store.compare(first.run_id, second.run_id)
    changed = comparison.assumptions.set_index("assumption")
    assert changed.loc["holding_days", "first run"] == "2"
    assert changed.loc["holding_days", "second run"] == "3"
    assert comparison.outcomes.loc[comparison.outcomes.outcome == "Completed trades", "first run"].iloc[0] == 1
    with pytest.raises(ValueError, match="two different"):
        store.compare(first.run_id, first.run_id)
