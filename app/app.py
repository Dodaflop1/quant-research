"""Streamlit research workbench: propose -> inspect -> confirm -> run."""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import streamlit as st
import altair as alt
from quant.equities.ai import interpret
from quant.equities.analysis import performance, statistical_test
from quant.equities.data import coverage_report, download_yfinance, load_csv
from quant.equities.engine import benchmark_curve, build_ledger
from quant.equities.robustness import sensitivity
from quant.equities.runs import RunStore
from quant.equities.schema import Hypothesis, Signal

st.set_page_config(page_title="Quant Research", layout="wide")
st.title("From market idea to reproducible experiment")
st.caption("AI interprets the idea. Deterministic code verifies the result. Research only — not investment advice.")

# Do not display results calculated under an earlier engine after the app reloads.
if st.session_state.get("engine_version") != 8:
    st.session_state.pop("confirmed", None)
    st.session_state.pop("saved_run", None)
    st.session_state["engine_version"] = 8

store = RunStore()

with st.sidebar:
    data_source = st.radio("Price data", ["Built-in demo", "Upload CSV", "Download Yahoo Finance"])
    uploaded = st.file_uploader("Daily-bar CSV (date, ticker, close, volume)", type="csv")
    if data_source == "Built-in demo":
        st.caption("Uses examples/sample_prices.csv — illustrative only.")
    elif data_source == "Download Yahoo Finance":
        st.caption("Downloads adjusted daily prices for the confirmed tickers. Save a CSV before citing results.")
    saved_index = store.list_runs()
    if not saved_index.empty:
        st.divider()
        st.caption("Saved research")
        saved_index["label"] = saved_index.apply(
            lambda row: f"{row['created_at'][:16]} · {row['run_id'][:8]} · {row['source']}", axis=1
        )
        saved_label = st.selectbox("Open a saved run", saved_index["label"])
        if st.button("Open saved run"):
            saved_id = saved_index.loc[saved_index.label == saved_label, "run_id"].iloc[0]
            saved = store.load(saved_id)
            st.session_state["confirmed"] = (
                saved.hypothesis, saved.initial_investment, "Saved snapshot", None, saved.split_date, None,
            )
            st.session_state["saved_run"] = saved
            st.session_state["latest_saved_run_id"] = saved.run_id
        if len(saved_index) >= 2:
            st.caption("Compare saved results")
            comparison_first = st.selectbox("First saved run", saved_index["label"], key="comparison_first")
            comparison_second = st.selectbox("Second saved run", saved_index["label"], index=1, key="comparison_second")
            if st.button("Compare saved runs"):
                first_id = saved_index.loc[saved_index.label == comparison_first, "run_id"].iloc[0]
                second_id = saved_index.loc[saved_index.label == comparison_second, "run_id"].iloc[0]
                st.session_state["comparison_run_ids"] = (first_id, second_id)

comparison_run_ids = st.session_state.get("comparison_run_ids")
if comparison_run_ids:
    try:
        comparison = store.compare(*comparison_run_ids)
        st.subheader("Saved-run comparison")
        st.caption("These are recorded outputs from each saved snapshot; neither result was recalculated.")
        outcome_view = comparison.outcomes.copy()
        percentage_rows = outcome_view.outcome.isin(["Net portfolio return", "Maximum drawdown"])
        outcome_view.loc[percentage_rows, ["first run", "second run"]] *= 100
        st.dataframe(outcome_view, use_container_width=True, hide_index=True)
        if comparison.assumptions.empty:
            st.info("The saved assumptions are identical. The records may differ only in when they were saved.")
        else:
            st.caption("Changed assumptions")
            st.dataframe(comparison.assumptions, use_container_width=True, hide_index=True)
    except ValueError as error:
        st.warning(str(error))

defaults = dict(name="5-day reversal", tickers=["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"], signal="reversal", lookback_days=1, threshold=0.05, consecutive_down_days=None, volume_ratio_min=None, volume_lookback_days=20, holding_days=5, direction="long", start_date=date(2023, 1, 1), end_date=date(2024, 12, 31), benchmark="SPY", transaction_cost_bps=10, top_n=5)
templates = {
    "Custom or AI-assisted": defaults,
    "High-volume selloff rebound": {**defaults, "name": "High-volume selloff rebound", "volume_ratio_min": 2.0},
    "SPY after three down sessions": {**defaults, "name": "SPY after three down sessions", "tickers": ["SPY"], "threshold": 0.01, "consecutive_down_days": 3, "holding_days": 5, "top_n": 1},
    "Large gain momentum": {**defaults, "name": "Large gain momentum", "signal": "momentum", "threshold": 0.05},
}
st.subheader("1. Experiment")
template_name = st.selectbox("Start from a supported template", list(templates))
idea = st.text_area("Research idea", "Do large one-day selloffs in a liquid U.S. stock basket rebound over the next five days?")
if st.button("Interpret with AI"):
    try:
        st.session_state["proposal"] = interpret(idea, templates[template_name]).model_dump(mode="json")
    except RuntimeError as error:
        st.warning(str(error))

saved_baseline = st.session_state.get("saved_run")
proposal = (
    saved_baseline.hypothesis.model_dump(mode="json")
    if saved_baseline and template_name == "Custom or AI-assisted"
    else st.session_state.get("proposal", defaults) if template_name == "Custom or AI-assisted" else templates[template_name]
)
if saved_baseline:
    st.caption(f"Editing saved snapshot {saved_baseline.run_id[:8]}. Confirming and saving creates a new revision; the original remains unchanged.")
with st.form("confirm_hypothesis"):
    name = st.text_input("Name", proposal["name"])
    tickers = st.text_input("Tickers", ", ".join(proposal["tickers"]))
    c1, c2, c3 = st.columns(3)
    signal = c1.selectbox("Signal", [item.value for item in Signal], index=[item.value for item in Signal].index(proposal["signal"]))
    lookback = c2.number_input("Lookback days", 1, 252, int(proposal["lookback_days"]))
    threshold = c3.number_input("Threshold (%)", 0.1, 100.0, float(proposal["threshold"]) * 100) / 100
    use_consecutive_down = st.checkbox("Require consecutive down sessions", value=proposal.get("consecutive_down_days") is not None)
    consecutive_down_days = None
    if use_consecutive_down:
        consecutive_down_days = st.number_input("Consecutive down sessions", 2, 20, int(proposal.get("consecutive_down_days") or 3))
        st.caption("This condition replaces the threshold when selecting signals.")
    use_volume = st.checkbox("Require unusually high volume", value=proposal.get("volume_ratio_min") is not None)
    volume_ratio = None
    volume_window = 20
    if use_volume:
        volume_columns = st.columns(2)
        volume_ratio = volume_columns[0].number_input("Minimum volume / average", 1.0, 100.0, float(proposal.get("volume_ratio_min") or 2.0), step=0.1)
        volume_window = volume_columns[1].number_input("Prior-volume window (days)", 5, 252, int(proposal.get("volume_lookback_days", 20)))
    c4, c6 = st.columns(2)
    hold = c4.number_input("Holding days", 1, 60, int(proposal["holding_days"]))
    costs = c6.number_input("Cost / side (bps)", 0.0, 200.0, float(proposal["transaction_cost_bps"]))
    benchmark = st.text_input("Benchmark ticker", proposal.get("benchmark", "SPY")).upper().strip()
    initial_investment = st.number_input("Starting investment ($)", min_value=100.0, value=float(saved_baseline.initial_investment) if saved_baseline else 10_000.0, step=100.0)
    start = st.date_input("Start", date.fromisoformat(str(proposal["start_date"])))
    end = st.date_input("End", date.fromisoformat(str(proposal["end_date"])))
    reserve_test = st.checkbox(
        "Compare an earlier and later period",
        value=True,
        help="Shows the result before and after a fixed split date. It is a historical comparison unless the later period was reserved before you saw it.",
    )
    split_date = None
    if reserve_test:
        suggested_split = min(max(date(2024, 1, 1), start), end)
        split_date = st.date_input(
            "First date of later comparison period",
            value=suggested_split,
            min_value=start,
            max_value=end,
        )
    confirmed = st.form_submit_button("Confirm hypothesis and run")

if confirmed:
    hypothesis = Hypothesis(name=name, tickers=tickers.split(","), signal=signal, lookback_days=lookback, threshold=threshold, consecutive_down_days=consecutive_down_days, volume_ratio_min=volume_ratio, volume_lookback_days=volume_window, holding_days=hold, direction="long", start_date=start, end_date=end, benchmark=benchmark, transaction_cost_bps=costs, top_n=len(tickers.split(",")))
    parent_run_id = saved_baseline.run_id if saved_baseline else None
    source_for_run = "Saved snapshot" if saved_baseline else data_source
    st.session_state["confirmed"] = (hypothesis, initial_investment, source_for_run, uploaded, split_date, parent_run_id)
    if not saved_baseline:
        st.session_state.pop("saved_run", None)

if "confirmed" in st.session_state:
    hypothesis, initial_investment, confirmed_source, confirmed_upload, split_date, parent_run_id = st.session_state["confirmed"]
    st.success("Confirmed: " + hypothesis.summary)
    try:
        if confirmed_source == "Upload CSV":
            if confirmed_upload is None:
                st.warning("Choose a CSV file in the sidebar, then confirm again.")
                st.stop()
            data = load_csv(confirmed_upload)
        elif confirmed_source == "Saved snapshot":
            saved = st.session_state.get("saved_run")
            if saved is None:
                raise ValueError("Saved run is no longer available in this session. Choose it again from Saved research.")
            data = saved.data
        elif confirmed_source == "Download Yahoo Finance":
            data = download_yfinance(
                hypothesis.tickers + [hypothesis.benchmark],
                str(hypothesis.start_date - timedelta(days=400)),
                str(hypothesis.end_date + timedelta(days=1)),
            )
        else:
            data = load_csv(Path("examples/sample_prices.csv"))
        st.subheader("2. Data")
        coverage = coverage_report(data, hypothesis.tickers + [hypothesis.benchmark], hypothesis.start_date, hypothesis.end_date)
        st.dataframe(coverage, use_container_width=True, hide_index=True, column_config={"coverage_pct": st.column_config.NumberColumn("Coverage", format="%.1f%%")})
        if (coverage.observations == 0).any():
            st.warning("One or more requested tickers have no prices in the selected period. Results may omit a stock or benchmark.")
        use_recorded_result = confirmed_source == "Saved snapshot" and parent_run_id is None
        result = saved.result if use_recorded_result else build_ledger(data, hypothesis, initial_investment)
        trades = result.trades
        daily_path = result.daily
        returns = daily_path["net_return"]
        if daily_path["active_positions"].sum() == 0:
            st.warning("No qualifying, fully observable positions were found for these settings. Try a longer date range, a lower threshold, or different tickers.")
        metrics, inference = performance(returns), statistical_test(returns)
        st.subheader("3. Results")
        if split_date and hypothesis.start_date < split_date <= hypothesis.end_date:
            exploratory = hypothesis.model_copy(update={"end_date": split_date - timedelta(days=1)})
            untouched = hypothesis.model_copy(update={"start_date": split_date})
            exploratory_result = build_ledger(data, exploratory, initial_investment)
            untouched_result = build_ledger(data, untouched, initial_investment)
            exploratory_path, untouched_path = exploratory_result.daily, untouched_result.daily
            exploratory_trades, untouched_trades = exploratory_result.trades, untouched_result.trades
            st.info(
                f"Research split: explore before {split_date:%b %d, %Y}; evaluate from that date onward. "
                "It is a valid test only when the later period was reserved before you saw its outcome."
            )
            explore_col, test_col = st.columns(2)
            explore_col.markdown("**Exploration period**")
            explore_col.metric("Qualifying events", len(exploratory_trades))
            exploration_metrics = performance(exploratory_path["net_return"])
            explore_col.metric("Net return", f"{exploration_metrics['total_return']:.1%}")
            explore_col.caption(f"Maximum drawdown: {exploration_metrics['max_drawdown']:.1%}")
            test_col.markdown("**Later comparison period**")
            test_col.metric("Qualifying events", len(untouched_trades))
            test_metrics = performance(untouched_path["net_return"])
            test_col.metric("Net return", f"{test_metrics['total_return']:.1%}")
            test_col.caption(f"Maximum drawdown: {test_metrics['max_drawdown']:.1%}")
            st.caption("Event count—not the number of calendar days—is the key sample-size check. The t-test below is descriptive because daily returns can overlap.")
        exposure = (daily_path["active_positions"] > 0).mean() if len(daily_path) else 0.0
        metric_a, metric_b, metric_c, metric_d = st.columns(4)
        metric_a.metric("Net portfolio return", f"{metrics['total_return']:.1%}")
        metric_b.metric("Maximum drawdown", f"{metrics['max_drawdown']:.1%}")
        metric_c.metric("Completed trades", len(trades))
        metric_d.metric("Days invested", f"{exposure:.0%}")
        curve = daily_path.copy()
        curve["cumulative_return_pct"] = (curve["portfolio_value"] / initial_investment - 1) * 100
        curve["drawdown_pct"] = (curve["portfolio_value"] / curve["portfolio_value"].cummax().clip(lower=initial_investment) - 1) * 100
        left, right = st.columns(2)
        benchmark = benchmark_curve(data, hypothesis.benchmark, curve.index, initial_investment)
        comparison = curve[["cumulative_return_pct"]].rename(columns={"cumulative_return_pct": "Strategy"}).join(
            benchmark[["benchmark_return_pct"]].rename(columns={"benchmark_return_pct": hypothesis.benchmark})
        )
        left.caption("Strategy and benchmark growth")
        left.altair_chart(
            alt.Chart(comparison.reset_index()).transform_fold(
                ["Strategy", hypothesis.benchmark], as_=["series", "return_pct"]
            ).mark_line(point=True).encode(
                x=alt.X("date:T", title="Signal date"),
                y=alt.Y("return_pct:Q", title="Cumulative return (%)"),
                color=alt.Color("series:N", title=""),
                tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("series:N", title="Series"), alt.Tooltip("return_pct:Q", title="Return", format=".2f")],
            ).properties(height=300),
            use_container_width=True,
        )
        if benchmark["benchmark_return_pct"].dropna().empty:
            left.caption(f"{hypothesis.benchmark} is not present in this dataset, so the chart shows the strategy only.")
        right.caption("Loss from the highest prior portfolio value")
        right.altair_chart(
            alt.Chart(curve.reset_index()).mark_line(point=True).encode(
                x=alt.X("date:T", title="Signal date"),
                y=alt.Y("drawdown_pct:Q", title="Drawdown (%)"),
                tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("drawdown_pct:Q", title="Drawdown", format=".2f")],
            ).properties(height=300),
            use_container_width=True,
        )
        st.caption("Signals are observed at a close, positions enter at the next available close, and the ledger includes cash, held shares, and both transaction-cost sides. Only positions that can fully close within the selected period are included.")
        st.dataframe(daily_path, use_container_width=True)
        st.subheader("Sensitivity (predefined nearby settings)")
        st.dataframe(sensitivity(data, hypothesis), use_container_width=True)
        st.subheader("Executed experiment records")
        st.dataframe(trades, use_container_width=True)
        if not result.skipped_signals.empty:
            st.caption(f"Excluded {len(result.skipped_signals)} signals that could not complete inside the selected period.")
        with st.expander("Details and statistical screen"):
            st.json({"performance": metrics, "one_sided_t_test": inference, "qualifying_events": len(trades)})
            st.caption("The t-test is descriptive: daily returns may include overlapping positions and are not guaranteed to be independent.")
        st.subheader("4. Saved research")
        if confirmed_source == "Saved snapshot" and parent_run_id is None:
            active_run_id = st.session_state.get("latest_saved_run_id")
            st.caption(f"Opened saved snapshot {active_run_id[:8]}. Its outputs use the recorded ledger rather than fresh market data.")
        elif st.button("Save this experiment"):
            saved = store.save(hypothesis, initial_investment, confirmed_source, split_date, data, result, parent_run_id)
            st.session_state["latest_saved_run_id"] = saved.run_id
            message = f"Saved run {saved.run_id[:8]} with its exact price snapshot."
            if parent_run_id:
                message += f" It is a revision of {parent_run_id[:8]}."
            st.success(message)
        active_run_id = st.session_state.get("latest_saved_run_id")
        if active_run_id:
            st.download_button(
                "Download reproducible research bundle",
                data=store.export_bundle(active_run_id),
                file_name=f"quant-research-{active_run_id[:8]}.zip",
                mime="application/zip",
            )
    except Exception as error:
        st.error(f"Could not run the experiment: {error}")
