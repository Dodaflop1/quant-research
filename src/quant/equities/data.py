"""Data boundaries: normalize input, validate it, and keep coverage explicit."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED = {"date", "ticker", "close", "volume"}


def load_csv(path: str | Path) -> pd.DataFrame:
    """Load a daily-bar CSV into the same validated format as provider data."""
    return load_frame(pd.read_csv(path))


def load_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize and validate daily close/volume data held in memory."""
    copy = frame.copy()
    copy.columns = [str(column).lower() for column in copy.columns]
    missing = REQUIRED - set(copy.columns)
    if missing:
        raise ValueError(f"Data is missing columns: {sorted(missing)}")
    copy["date"] = pd.to_datetime(copy["date"], errors="coerce", utc=True).dt.tz_localize(None)
    copy["ticker"] = copy["ticker"].astype(str).str.upper().str.strip()
    for column in ("close", "volume"):
        copy[column] = pd.to_numeric(copy[column], errors="coerce")
    if copy[["date", "ticker", "close", "volume"]].isna().any().any() or (copy.ticker == "").any():
        raise ValueError("date, ticker, close, and volume must all be present and valid")
    if not np.isfinite(copy.close).all() or not np.isfinite(copy.volume).all():
        raise ValueError("close and volume must be finite numbers")
    if (copy.close <= 0).any() or (copy.volume < 0).any():
        raise ValueError("close must be positive and volume non-negative")
    return copy.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"]).reset_index(drop=True)


def download_yfinance(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Download adjusted bars. ``end`` is provider-exclusive; persist results before citing them."""
    import yfinance as yf

    frames = []
    for ticker in sorted({item.upper().strip() for item in tickers if item.strip()}):
        raw = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
        if raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index().rename(columns={"Date": "date", "Close": "close", "Volume": "volume"})
        raw["ticker"] = ticker
        frames.append(raw[["date", "ticker", "close", "volume"]])
    if not frames:
        raise ValueError("Provider returned no data for the requested tickers and dates")
    return load_frame(pd.concat(frames, ignore_index=True))


def coverage_report(data: pd.DataFrame, tickers: list[str], start: object, end: object) -> pd.DataFrame:
    """Report observed rows and date coverage for every requested ticker."""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    selected = data[(data.date >= start_ts) & (data.date <= end_ts)]
    expected_days = max(1, selected.date.nunique())
    rows = []
    for ticker in sorted({item.upper().strip() for item in tickers if item.strip()}):
        values = selected[selected.ticker == ticker]
        rows.append(
            {
                "ticker": ticker,
                "observations": len(values),
                "first_date": values.date.min() if not values.empty else pd.NaT,
                "last_date": values.date.max() if not values.empty else pd.NaT,
                "coverage_pct": len(values) / expected_days * 100,
            }
        )
    return pd.DataFrame(rows)
