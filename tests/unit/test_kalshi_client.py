"""Tests for Kalshi request signing and order book parsing.

None of these touch the network or need credentials: signing is checked against
a throwaway key, and parsing is checked against payloads shaped like the
documented responses.
"""

from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from quant.common.api.kalshi import (
    DEMO_BASE,
    PROD_BASE,
    KalshiAPIError,
    KalshiClient,
    parse_orderbook,
    sign_request,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_signature_verifies_against_public_key(key):
    ts, method, path = 1703123456789, "GET", "/trade-api/v2/portfolio/balance"
    import base64

    sig = base64.b64decode(sign_request(key, ts, method, path))
    # Raises InvalidSignature if the message does not match.
    key.public_key().verify(
        sig,
        f"{ts}{method}{path}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_method_is_upper_cased(key):
    assert sign_request(key, 1, "get", "/x") != ""
    # PSS is randomised, so signatures are not comparable; verify instead.
    import base64

    sig = base64.b64decode(sign_request(key, 1, "get", "/x"))
    key.public_key().verify(
        sig,
        b"1GET/x",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_query_string_is_excluded_from_signed_path(key):
    """Signing the query string is the usual cause of a 401 here."""
    client = KalshiClient(api_key_id="abc", private_key=key)
    headers = client._headers("GET", f"{PROD_BASE}/markets/FOO/orderbook?depth=0")

    import base64

    ts = int(headers["KALSHI-ACCESS-TIMESTAMP"])
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(
        sig,
        f"{ts}GET/trade-api/v2/markets/FOO/orderbook".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_required_headers_present(key):
    client = KalshiClient(api_key_id="key-id-123", private_key=key)
    headers = client._headers("GET", f"{PROD_BASE}/events")
    assert headers["KALSHI-ACCESS-KEY"] == "key-id-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert headers["KALSHI-ACCESS-SIGNATURE"]


def test_timestamp_is_milliseconds(key):
    client = KalshiClient(api_key_id="k", private_key=key)
    ts = int(client._headers("GET", f"{PROD_BASE}/events")["KALSHI-ACCESS-TIMESTAMP"])
    # Milliseconds since epoch is 13 digits until the year 2286.
    assert 10**12 < ts < 10**14


def test_bases_are_distinct():
    assert PROD_BASE != DEMO_BASE
    assert PROD_BASE.endswith("/trade-api/v2")
    assert DEMO_BASE.endswith("/trade-api/v2")


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True),
                                              (400, False), (401, False), (404, False)])
def test_error_retry_classification(status, retryable):
    assert KalshiAPIError(status, "", "/x").retryable is retryable


# ---------------------------------------------------------------------------
# Order book parsing
# ---------------------------------------------------------------------------


def test_no_bids_become_yes_asks():
    """The API returns bids on both sides; a NO bid at q is a YES ask at 1-q."""
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.6400", "100.00"], ["0.6300", "250.00"]],
            "no_dollars": [["0.3400", "80.00"], ["0.3300", "400.00"]],
        }
    }
    book = parse_orderbook(payload, "FED-B4", T0)

    assert [lvl.price for lvl in book.yes_bids] == [64.0, 63.0]
    # 1 - 0.34 = 0.66 and 1 - 0.33 = 0.67, sorted ascending as asks.
    assert [lvl.price for lvl in book.yes_asks] == [66.0, 67.0]
    assert [lvl.size for lvl in book.yes_asks] == [80.0, 400.0]
    assert book.spread == pytest.approx(2.0)


def test_prices_are_dollars_not_cents():
    """A dollar string of 0.15 is 15 cents, not 0.15 cents."""
    payload = {"orderbook_fp": {"yes_dollars": [["0.1500", "10.00"]], "no_dollars": []}}
    book = parse_orderbook(payload, "X", T0)
    assert book.best_yes_bid == pytest.approx(15.0)


def test_empty_book_parses():
    book = parse_orderbook({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}, "X", T0)
    assert book.best_yes_bid is None and book.best_yes_ask is None


def test_one_sided_book_parses():
    payload = {"orderbook_fp": {"yes_dollars": [["0.2000", "5.00"]], "no_dollars": []}}
    book = parse_orderbook(payload, "X", T0)
    assert book.best_yes_bid == 20.0
    assert book.best_yes_ask is None
    assert book.spread is None


def test_zero_size_levels_dropped():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.6400", "100.00"], ["0.6300", "0.00"]],
            "no_dollars": [],
        }
    }
    assert len(parse_orderbook(payload, "X", T0).yes_bids) == 1


def test_ladders_are_sorted_regardless_of_input_order():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.6300", "1.00"], ["0.6400", "1.00"]],
            "no_dollars": [["0.3300", "1.00"], ["0.3400", "1.00"]],
        }
    }
    book = parse_orderbook(payload, "X", T0)
    assert [lvl.price for lvl in book.yes_bids] == [64.0, 63.0]
    assert [lvl.price for lvl in book.yes_asks] == [66.0, 67.0]


def test_legacy_integer_cent_shape_still_parses():
    payload = {"orderbook": {"yes": [[64, 100]], "no": [[34, 80]]}}
    book = parse_orderbook(payload, "X", T0)
    assert book.best_yes_bid == 64.0
    assert book.best_yes_ask == 66.0
