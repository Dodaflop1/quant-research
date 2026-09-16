"""Data boundaries: normalize input, validate it, and keep provenance explicit."""
from __future__ import annotations

from pathlib import Path
import pandas as pd

REQUIRED = {"date", "ticker", "close", "volume"}


def load_csv(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = REQUIRED - set(frame.columns.str.lower())
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")
    frame.columns = [column.lower() for column in frame.columns]
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None)
    frame["ticker"] = frame["ticker"].str.upper()
    frame = frame.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"])
    if (frame["close"] <= 0).any() or (frame["volume"] < 0).any():
        raise ValueError("close must be positive and volume non-negative")
    return frame.reset_index(drop=True)


def download_yfinance(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Optional convenience provider. Persist its result before claiming reproducibility."""
    import yfinance as yf
    frames = []
    for ticker in tickers:
        raw = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
        if raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index().rename(columns={"Date": "date", "Close": "close", "Volume": "volume"})
        raw["ticker"] = ticker.upper()
        frames.append(raw[["date", "ticker", "close", "volume"]])
    if not frames:
        raise ValueError("Provider returned no data for the requested tickers and dates")
    return load_frame(pd.concat(frames, ignore_index=True))


def load_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the same validation to data already held in memory."""
    copy = frame.copy()
    copy.columns = [column.lower() for column in copy.columns]
    missing = REQUIRED - set(copy.columns)
    if missing:
        raise ValueError(f"Data is missing columns: {sorted(missing)}")
    copy["date"] = pd.to_datetime(copy["date"], utc=True).dt.tz_localize(None)
    copy["ticker"] = copy["ticker"].astype(str).str.upper()
    return copy.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"]).reset_index(drop=True)
