"""Kalshi REST client.

Auth scheme, base URLs and response shapes verified against docs.kalshi.com on
2026-08-23. Two details are easy to get wrong from memory and are load-bearing
here:

* ``GET /markets/{ticker}/orderbook`` returns **bids only on both sides**.
  ``yes_dollars`` is the YES bid ladder and ``no_dollars`` is the NO bid ladder;
  asks are implicit, because a NO bid at ``q`` is a YES ask at ``1 - q``.
* Prices are dollar-denominated strings such as ``"0.1500"``, not integer cents.

The client is synchronous on purpose. The collector polls on the order of tens
of markets every tens of seconds, where async buys nothing and costs
debuggability in an unattended process.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from quant.common.db.schema import OrderBookLevel, OrderBookSnapshot, Trade

log = logging.getLogger(__name__)

PROD_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"

# Trades live behind two endpoints split at a rolling cutoff; see
# :meth:`KalshiClient.iter_all_trades`.
LIVE_TRADES_PATH = "/markets/trades"
HISTORICAL_TRADES_PATH = "/historical/trades"


class KalshiAuthError(RuntimeError):
    pass


class KalshiAPIError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"{status} on {path}: {body[:400]}")
        self.status = status
        self.body = body
        self.path = path

    @property
    def retryable(self) -> bool:
        return self.status == 429 or self.status >= 500


def load_private_key(path: str | Path) -> rsa.RSAPrivateKey:
    """Load an unencrypted PEM RSA private key from disk."""
    data = Path(path).expanduser().read_bytes()
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise KalshiAuthError(f"{path} is not an RSA private key")
    return key


def sign_request(
    private_key: rsa.RSAPrivateKey, timestamp_ms: int, method: str, path: str
) -> str:
    """Sign ``timestamp + METHOD + path`` with RSA-PSS/SHA256, base64 encoded.

    ``path`` must exclude the query string but include the ``/trade-api/v2``
    prefix. Signing the query string is the most common cause of a 401 here.
    """
    message = f"{timestamp_ms}{method.upper()}{path}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


class _TokenBucket:
    """Simple token bucket.

    Kalshi does not publish a single global rate limit; tiers differ. The
    default here is deliberately conservative. Raise it only after checking the
    limit that applies to the account.
    """

    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.capacity = burst
        self._tokens = float(burst)
        self._last = time.monotonic()

    def take(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens < 1.0:
            time.sleep((1.0 - self._tokens) / self.rate)
            self._tokens = 0.0
            self._last = time.monotonic()
        else:
            self._tokens -= 1.0


@dataclass(frozen=True)
class RawResponse:
    """A response plus the metadata needed to reinterpret it later.

    Order book data cannot be re-collected, so the raw payload is preserved
    verbatim alongside the receive time rather than only its parsed form.
    """

    path: str
    params: dict
    requested_at: datetime
    received_at: datetime
    payload: Any


class KalshiClient:
    def __init__(
        self,
        api_key_id: str,
        private_key: rsa.RSAPrivateKey,
        base_url: str = PROD_BASE,
        rate_per_sec: float = 8.0,
        burst: int = 16,
        timeout: float = 15.0,
        max_retries: int = 5,
    ):
        self.api_key_id = api_key_id
        self.private_key = private_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._bucket = _TokenBucket(rate_per_sec, burst)
        self._session = requests.Session()

    @classmethod
    def from_env(cls, demo: bool = False, **kwargs) -> "KalshiClient":
        """Build from ``KALSHI_API_KEY_ID`` and ``KALSHI_PRIVATE_KEY_PATH``."""
        import os

        key_id = os.environ.get("KALSHI_API_KEY_ID")
        key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if not key_id or not key_path:
            raise KalshiAuthError(
                "KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH must be set "
                "(load a .env first, e.g. via python-dotenv)"
            )
        return cls(
            api_key_id=key_id,
            private_key=load_private_key(key_path),
            base_url=DEMO_BASE if demo else PROD_BASE,
            **kwargs,
        )

    def _headers(self, method: str, full_url: str) -> dict[str, str]:
        # Sign the path only; the query string is excluded.
        path = urlsplit(full_url).path
        ts = int(time.time() * 1000)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "KALSHI-ACCESS-SIGNATURE": sign_request(self.private_key, ts, method, path),
            "Accept": "application/json",
        }

    def get(self, path: str, params: Optional[dict] = None) -> RawResponse:
        url = f"{self.base_url}{path}"
        params = params or {}
        last_err: Optional[Exception] = None

        for attempt in range(self.max_retries):
            self._bucket.take()
            requested_at = datetime.now(timezone.utc)
            try:
                resp = self._session.get(
                    url,
                    params=params,
                    headers=self._headers("GET", url),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_err = exc
                self._backoff(attempt, str(exc))
                continue

            received_at = datetime.now(timezone.utc)
            if resp.status_code == 200:
                return RawResponse(
                    path=path,
                    params=params,
                    requested_at=requested_at,
                    received_at=received_at,
                    payload=resp.json(),
                )

            err = KalshiAPIError(resp.status_code, resp.text, path)
            if not err.retryable:
                raise err
            last_err = err
            self._backoff(attempt, f"HTTP {resp.status_code}")

        raise KalshiAPIError(0, f"exhausted retries: {last_err}", path)

    def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(30.0, 2.0**attempt)
        log.warning("retrying in %.1fs after %s", delay, reason)
        time.sleep(delay)

    # -- endpoints ---------------------------------------------------------

    def iter_events(
        self, status: str = "open", with_nested_markets: bool = True
    ) -> Iterator[dict]:
        """Yield events, following the pagination cursor."""
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "limit": 200,
                "status": status,
                "with_nested_markets": str(with_nested_markets).lower(),
            }
            if cursor:
                params["cursor"] = cursor
            raw = self.get("/events", params)
            body = raw.payload
            for event in body.get("events", []):
                yield event
            cursor = body.get("cursor") or None
            if not cursor:
                return

    def get_orderbook_raw(self, ticker: str, depth: int = 0) -> RawResponse:
        """Raw order book. ``depth=0`` requests all levels."""
        return self.get(f"/markets/{ticker}/orderbook", {"depth": depth})

    def iter_trade_pages(
        self,
        path: str,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 1000,
    ) -> Iterator[RawResponse]:
        """Yield raw trade pages, following the pagination cursor.

        Pages are yielded rather than individual trades so the caller can write
        each response verbatim before parsing anything - the same
        raw-payload-first discipline the order book collector uses.

        ``path`` is either :data:`LIVE_TRADES_PATH` or
        :data:`HISTORICAL_TRADES_PATH`; see :func:`iter_all_trades` for why both
        exist and when each applies.
        """
        cursor: Optional[str] = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            if min_ts is not None:
                params["min_ts"] = int(min_ts)
            if max_ts is not None:
                params["max_ts"] = int(max_ts)
            if cursor:
                params["cursor"] = cursor

            raw = self.get(path, params)
            yield raw

            cursor = (raw.payload or {}).get("cursor") or None
            if not cursor:
                return
            if cursor in seen:
                # A server that echoes a cursor forever turns an unattended
                # backfill into an infinite loop that writes duplicate pages
                # until the disk fills. Cheap to guard, expensive to discover.
                log.warning("%s repeated cursor %r, stopping this window", path, cursor)
                return
            seen.add(cursor)

    def iter_all_trades(
        self,
        ticker: Optional[str] = None,
        min_ts: Optional[int] = None,
        max_ts: Optional[int] = None,
        limit: int = 1000,
    ) -> Iterator[RawResponse]:
        """Yield trade pages from both the live and historical endpoints.

        Kalshi splits trades across two endpoints at a rolling cutoff: roughly
        the last three months are served by ``/markets/trades`` and everything
        older by ``/historical/trades``. The cutoff moves daily and is not
        published as a timestamp, so hardcoding one would silently start losing
        the seam as the window slides.

        Instead both endpoints are queried over the same window and the caller
        deduplicates on ``trade_id``. The overlap costs a few redundant requests
        and removes an entire class of quiet data loss.
        """
        for path in (LIVE_TRADES_PATH, HISTORICAL_TRADES_PATH):
            try:
                yield from self.iter_trade_pages(
                    path, ticker=ticker, min_ts=min_ts, max_ts=max_ts, limit=limit
                )
            except KalshiAPIError as exc:
                # One endpoint refusing a window must not abort the other. A 404
                # here usually means the window falls entirely on the far side
                # of the cutoff, which is expected, not exceptional.
                log.warning("%s failed for window: %s", path, exc)


# -- parsing ---------------------------------------------------------------


def _ladder(entries: Any) -> list[tuple[float, float]]:
    """Parse ``[["0.1500", "100.00"], ...]`` into (price_cents, size)."""
    out: list[tuple[float, float]] = []
    for entry in entries or []:
        price_dollars, size = entry[0], entry[1]
        out.append((float(price_dollars) * 100.0, float(size)))
    return out


def parse_orderbook(
    payload: dict, ticker: str, observed_at: datetime
) -> OrderBookSnapshot:
    """Convert a raw order book payload into an :class:`OrderBookSnapshot`.

    The API returns bid ladders on both sides. NO bids are converted to YES
    asks via ``ask = 100 - no_bid``, which is what makes a single YES-terms
    book well defined.
    """
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}

    yes_raw = book.get("yes_dollars")
    no_raw = book.get("no_dollars")
    if yes_raw is None and no_raw is None:
        # Legacy integer-cent shape, kept as a fallback.
        yes_bids = [(float(p), float(s)) for p, s in (book.get("yes") or [])]
        no_bids = [(float(p), float(s)) for p, s in (book.get("no") or [])]
    else:
        yes_bids = _ladder(yes_raw)
        no_bids = _ladder(no_raw)

    yes_asks = [(100.0 - price, size) for price, size in no_bids]

    yes_bids.sort(key=lambda x: -x[0])
    yes_asks.sort(key=lambda x: x[0])

    return OrderBookSnapshot(
        timestamp=observed_at,
        contract_id=ticker,
        yes_bids=[OrderBookLevel(price=p, size=s) for p, s in yes_bids if s > 0],
        yes_asks=[OrderBookLevel(price=p, size=s) for p, s in yes_asks if s > 0],
    )


def _first_present(record: dict, *names: str) -> Any:
    """Return the first field that is present and not None.

    Live Kalshi payloads carry unit-suffixed field names (``count_fp``,
    ``yes_price_dollars``) that do not appear in some of the documentation. The
    bare names are kept as fallbacks rather than assumed absent: a parser that
    reads only the documented names returned zero rows against production on
    2026-08-23, which is the kind of failure that looks like "no data" rather
    than "wrong parser".
    """
    for name in names:
        if record.get(name) is not None:
            return record[name]
    return None


def _trade_time(value: Any) -> datetime:
    """Parse ``created_time``, which may be ISO 8601 or a Unix timestamp."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_trade(record: dict) -> Trade:
    """Convert one raw trade print into a :class:`Trade`.

    Taker side is recorded in YES terms. Kalshi reports ``taker_outcome_side``
    as the outcome the aggressor bought, so a taker who bought NO is a seller of
    YES: buying NO at ``q`` and selling YES at ``100 - q`` are the same trade
    seen from opposite sides of the book. Collapsing both into a single YES-terms
    direction is what makes buy- and sell-initiated flow comparable across
    markets, which the bivariate Hawkes fit depends on.
    """
    price_dollars = _first_present(record, "yes_price_dollars")
    if price_dollars is not None:
        price_cents = float(price_dollars) * 100.0
    else:
        raw_price = _first_present(record, "yes_price")
        if raw_price is None:
            raise ValueError(f"trade has no YES price field: {sorted(record)}")
        price_cents = float(raw_price)

    size = _first_present(record, "count_fp", "count")
    if size is None:
        raise ValueError(f"trade has no size field: {sorted(record)}")

    outcome = (record.get("taker_outcome_side") or "").lower()
    if outcome == "yes":
        taker_side = "buy"
    elif outcome == "no":
        taker_side = "sell"
    else:
        raise ValueError(f"unrecognised taker_outcome_side {outcome!r}")

    return Trade(
        timestamp=_trade_time(_first_present(record, "created_time", "ts")),
        contract_id=record["ticker"],
        price=price_cents,
        size=float(size),
        taker_side=taker_side,
    )
