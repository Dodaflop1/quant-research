"""Streamlit research workbench: propose -> inspect -> confirm -> run."""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
import streamlit as st
import altair as alt
from quant.equities.ai import interpret
from quant.equities.analysis import performance, statistical_test
from quant.equities.data import download_yfinance, load_csv
from quant.equities.engine import equity_curve, portfolio_daily_path, run_backtest
from quant.equities.robustness import sensitivity
from quant.equities.schema import Direction, Hypothesis, Signal

st.set_page_config(page_title="Quant Research", layout="wide")
st.title("From market idea to reproducible experiment")
st.caption("AI interprets the idea. Deterministic code verifies the result. Research only — not investment advice.")

# Do not display results calculated under an earlier engine after the app reloads.
if st.session_state.get("engine_version") != 4:
    st.session_state.pop("confirmed", None)
    st.session_state["engine_version"] = 4

with st.sidebar:
    data_source = st.radio("Price data", ["Built-in demo", "Upload CSV", "Download Yahoo Finance"])
    uploaded = st.file_uploader("Daily-bar CSV (date, ticker, close, volume)", type="csv")
    if data_source == "Built-in demo":
        st.caption("Uses examples/sample_prices.csv — illustrative only.")
    elif data_source == "Download Yahoo Finance":
        st.caption("Downloads adjusted daily prices for the confirmed tickers. Save a CSV before citing results.")

defaults = dict(name="5-day reversal", tickers=["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"], signal="reversal", lookback_days=1, threshold=0.05, volume_ratio_min=None, volume_lookback_days=20, holding_days=5, direction="long", start_date=date(2023, 1, 1), end_date=date(2024, 12, 31), benchmark="SPY", transaction_cost_bps=10, top_n=5)
st.subheader("1. Describe or define the hypothesis")
idea = st.text_area("Research idea", "Do large one-day selloffs in a liquid U.S. stock basket rebound over the next five days?")
if st.button("Interpret with AI"):
    try:
        st.session_state["proposal"] = interpret(idea, defaults).model_dump(mode="json")
    except RuntimeError as error:
        st.warning(str(error))

proposal = st.session_state.get("proposal", defaults)
with st.form("confirm_hypothesis"):
    name = st.text_input("Name", proposal["name"])
    tickers = st.text_input("Tickers", ", ".join(proposal["tickers"]))
    c1, c2, c3 = st.columns(3)
    signal = c1.selectbox("Signal", [item.value for item in Signal], index=[item.value for item in Signal].index(proposal["signal"]))
    lookback = c2.number_input("Lookback days", 1, 252, int(proposal["lookback_days"]))
    threshold = c3.number_input("Threshold (%)", 0.1, 100.0, float(proposal["threshold"]) * 100) / 100
    use_volume = st.checkbox("Require unusually high volume", value=proposal.get("volume_ratio_min") is not None)
    volume_ratio = None
    volume_window = 20
    if use_volume:
        volume_columns = st.columns(2)
        volume_ratio = volume_columns[0].number_input("Minimum volume / average", 1.0, 100.0, float(proposal.get("volume_ratio_min") or 2.0), step=0.1)
        volume_window = volume_columns[1].number_input("Prior-volume window (days)", 5, 252, int(proposal.get("volume_lookback_days", 20)))
    c4, c5, c6 = st.columns(3)
    hold = c4.number_input("Holding days", 1, 60, int(proposal["holding_days"]))
    direction = c5.selectbox("Direction", [item.value for item in Direction], index=[item.value for item in Direction].index(proposal["direction"]))
    costs = c6.number_input("Cost / side (bps)", 0.0, 200.0, float(proposal["transaction_cost_bps"]))
    initial_investment = st.number_input("Starting investment ($)", min_value=100.0, value=10_000.0, step=100.0)
    start = st.date_input("Start", date.fromisoformat(str(proposal["start_date"])))
    end = st.date_input("End", date.fromisoformat(str(proposal["end_date"])))
    reserve_test = st.checkbox(
        "Reserve an untouched test period",
        value=True,
        help="Shows the result before and after a fixed split date. Do not change the rule after viewing the test result.",
    )
    split_date = None
    if reserve_test:
        suggested_split = min(max(date(2024, 1, 1), start), end)
        split_date = st.date_input(
            "First date of untouched test period",
            value=suggested_split,
            min_value=start,
            max_value=end,
        )
    confirmed = st.form_submit_button("Confirm hypothesis and run")

if confirmed:
    hypothesis = Hypothesis(name=name, tickers=tickers.split(","), signal=signal, lookback_days=lookback, threshold=threshold, volume_ratio_min=volume_ratio, volume_lookback_days=volume_window, holding_days=hold, direction=direction, start_date=start, end_date=end, transaction_cost_bps=costs, top_n=len(tickers.split(",")))
    st.session_state["confirmed"] = (hypothesis, initial_investment, data_source, uploaded, split_date)

if "confirmed" in st.session_state:
    hypothesis, initial_investment, confirmed_source, confirmed_upload, split_date = st.session_state["confirmed"]
    st.success("Confirmed: " + hypothesis.summary)
    try:
        if confirmed_source == "Upload CSV":
            if confirmed_upload is None:
                st.warning("Choose a CSV file in the sidebar, then confirm again.")
                st.stop()
            data = load_csv(confirmed_upload)
        elif confirmed_source == "Download Yahoo Finance":
            data = download_yfinance(
                hypothesis.tickers,
                str(hypothesis.start_date),
                str(hypothesis.end_date),
            )
        else:
            data = load_csv(Path("examples/sample_prices.csv"))
        trades = run_backtest(data, hypothesis)
        daily_path = portfolio_daily_path(data, hypothesis)
        returns = daily_path["net_return"]
        if daily_path["active_positions"].sum() == 0:
            st.warning("No qualifying, fully observable positions were found for these settings. Try a longer date range, a lower threshold, or different tickers.")
        metrics, inference = performance(returns), statistical_test(returns)
        st.subheader("2. Results")
        if split_date and hypothesis.start_date < split_date <= hypothesis.end_date:
            exploratory = hypothesis.model_copy(update={"end_date": split_date - timedelta(days=1)})
            untouched = hypothesis.model_copy(update={"start_date": split_date})
            exploratory_path = portfolio_daily_path(data, exploratory)
            untouched_path = portfolio_daily_path(data, untouched)
            exploratory_trades = run_backtest(data, exploratory)
            untouched_trades = run_backtest(data, untouched)
            st.info(
                f"Research split: explore before {split_date:%b %d, %Y}; evaluate from that date onward. "
                "Treat the second panel as a test only if you keep this rule unchanged."
            )
            explore_col, test_col = st.columns(2)
            explore_col.markdown("**Exploration period**")
            explore_col.metric("Qualifying events", len(exploratory_trades))
            explore_col.json(performance(exploratory_path["net_return"]))
            test_col.markdown("**Untouched test period**")
            test_col.metric("Qualifying events", len(untouched_trades))
            test_col.json(performance(untouched_path["net_return"]))
            st.caption("Event count—not the number of calendar days—is the key sample-size check. The t-test below is descriptive because daily returns can overlap.")
        st.json({"performance": metrics, "one_sided_t_test": inference, "qualifying_events": len(trades)})
        curve = equity_curve(returns, initial_investment)
        left, right = st.columns(2)
        left.caption("Cumulative return after estimated costs")
        left.altair_chart(
            alt.Chart(curve.reset_index()).mark_line(point=True).encode(
                x=alt.X("date:T", title="Signal date"),
                y=alt.Y("cumulative_return_pct:Q", title="Cumulative return (%)"),
                tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("cumulative_return_pct:Q", title="Return", format=".2f")],
            ).properties(height=300),
            use_container_width=True,
        )
        right.caption("Loss from the highest prior portfolio value")
        right.altair_chart(
            alt.Chart(curve.reset_index()).mark_line(point=True).encode(
                x=alt.X("date:T", title="Signal date"),
                y=alt.Y("drawdown_pct:Q", title="Drawdown (%)"),
                tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("drawdown_pct:Q", title="Drawdown", format=".2f")],
            ).properties(height=300),
            use_container_width=True,
        )
        st.caption("The portfolio stays flat on days without active positions. Positions begin on the trading day after a signal, not at the same close that generated it.")
        st.dataframe(daily_path, use_container_width=True)
        st.subheader("Sensitivity (predefined nearby settings)")
        st.dataframe(sensitivity(data, hypothesis), use_container_width=True)
        st.subheader("Executed experiment records")
        st.dataframe(trades, use_container_width=True)
    except Exception as error:
        st.error(f"Could not run the experiment: {error}")
