"""Fair value for daily-high temperature contracts.

Kalshi lists, for each of several cities, a family of mutually exclusive
contracts on the official daily maximum temperature: ``<= 69``, ``70 to 71``,
``72 to 73``, ... , ``>= 86``.

**Settlement source varies by series and decides whether this model can be
fitted at all.** Measured off each series' own `settlement_sources` field, not
from the help centre:

- Most daily temperature series — ``KXHIGHNY``, ``KXHIGHCHI``, ``KXHIGHTPHX``
  and the bulk of the rest — settle on **The Weather Company**, a proprietary
  product with no public history. Their observed half cannot be retrieved for
  free, and the vendor is itself a forecaster, so the settlement source and the
  obvious model input are not independent.
- A smaller set — ``KXHIGHOU``, ``KXDENHIGH``, ``KXPHILHIGH``, ``KXDVHIGH`` and
  the legacy ``HIGH*`` series — settle on the NWS **Daily Climate Report**, a
  free product reporting whole degrees Fahrenheit. Those are the ones this
  model can be fitted and scored on from public data.

Either way the settled value is a whole degree, which is what
`Bucket.continuous_bounds` rests on. It is specifically NOT the station
observation feed, which reports Celsius and can miss a spike between hourly
readings.

Two consequences the caller owns rather than this module:

- The report's day runs midnight to midnight local, **except under Daylight
  Saving Time, when it runs 01:00 to 00:59 the following day**. A collector
  that pairs forecasts with calendar-day maxima is wrong for eight months of
  the year, in a way that shows up as forecast error rather than as a bug.
- Lead time must be measured to the *close* of that window, not to midnight.

The public forecast is the obvious input. Turning it into a price is not
obvious, and almost all of the work is in two places that are easy to get
wrong and impossible to see once they are wrong:

**The forecast is a point, the contract is a distribution.** A forecast of 74F
says nothing about ``72 to 73`` until you know how far forecasts of 74 miss.
That spread is the entire model. It is measured, per lead time, from
(forecast, observed) pairs — never assumed. `ErrorModel.fit` refuses to return
a model from fewer than `MIN_SAMPLES` pairs rather than returning a wide one,
because a plausible number from an unmeasured spread is the failure this
project keeps paying for.

**The observation is rounded and the bucket is not.** The station reports whole
degrees, so the bucket ``72 to 73`` is the event ``round(T) in {72, 73}``,
which is ``T in [71.5, 73.5)`` — not ``[72, 73]``. The difference is one degree
of width, roughly a third of a typical day-ahead forecast error, and it biases
every bucket in the family the same direction. Boundaries are at the halves
throughout, and there is a test that fails if anyone "simplifies" them.

What this model does NOT capture
--------------------------------
`stderr_of_probability` propagates the uncertainty in the *fitted parameters*
of the error distribution. It says nothing about whether the distribution has
the right shape. A Gaussian error model with a well-estimated sigma will report
a small standard error on a tail bucket it is systematically wrong about,
because tail probabilities are exactly where the Gaussian assumption fails and
exactly where the parameter uncertainty is smallest. Fit `family="empirical"`
once there are enough pairs, and treat a Gaussian tail probability below about
0.05 as unquoted rather than small.

This is the same distinction the diffusion work ran into: a diagnostic that
passes validates the fit, not the mechanism.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional, Sequence

import numpy as np

__all__ = [
    "Bucket",
    "ErrorModel",
    "ErrorSample",
    "UncalibratedModel",
    "bucket_probabilities",
    "buckets_from_edges",
    "parse_lead_hours",
]

#: Below this many pairs in a lead-time bucket the spread is not measured, and
#: the model refuses rather than widening. 30 is not a magic number so much as
#: the point where the standard error of a fitted sigma drops under 13%.
MIN_SAMPLES = 30

#: Lead-time bucket edges in hours. Forecast error grows with lead time, so a
#: single pooled error distribution would be too wide at one day and too narrow
#: at five. Edges are open on the right: [0, 12), [12, 24), ...
LEAD_BUCKET_EDGES: tuple[float, ...] = (0.0, 12.0, 24.0, 48.0, 72.0, 120.0, 168.0)


class UncalibratedModel(RuntimeError):
    """Raised when a probability is requested from a model that has not been
    fitted on enough data to have one.

    Deliberately an exception and not a wide interval. A caller that catches
    this has to decide what to do; a caller handed a 40%-wide interval will
    trade on its midpoint.
    """


@dataclass(frozen=True)
class ErrorSample:
    """One verified forecast: what was predicted, what happened, how far out.

    ``observed_f`` is the integer the station reported. ``forecast_f`` may be
    fractional — some products carry a decimal — and is not rounded here,
    because rounding the input would fold a second discretisation into a model
    whose whole subject is the first one.
    """

    lead_hours: float
    forecast_f: float
    observed_f: float
    station: str = ""

    @property
    def error(self) -> float:
        """Observed minus forecast. Positive means the day beat the forecast."""
        return float(self.observed_f) - float(self.forecast_f)


@dataclass(frozen=True)
class Bucket:
    """One contract in a temperature family, in whole degrees.

    ``low`` and ``high`` are inclusive integer bounds; ``None`` means open.
    ``<= 69`` is ``Bucket(None, 69)`` and ``>= 86`` is ``Bucket(86, None)``.
    """

    low: Optional[int]
    high: Optional[int]
    ticker: str = ""

    def __post_init__(self) -> None:
        if self.low is None and self.high is None:
            raise ValueError("a bucket open at both ends is the whole line")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError(f"empty bucket: {self.low} > {self.high}")

    @property
    def continuous_bounds(self) -> tuple[float, float]:
        """The half-open interval of true temperature that rounds into this bucket.

        THE half-degree offset. ``72 to 73`` is [71.5, 73.5), because a true
        73.4 reports as 73 and a true 73.6 reports as 74.
        """
        lo = -math.inf if self.low is None else self.low - 0.5
        hi = math.inf if self.high is None else self.high + 0.5
        return (lo, hi)

    def contains_observation(self, observed_f: float) -> bool:
        lo, hi = self.continuous_bounds
        return lo <= float(observed_f) < hi


def buckets_from_edges(edges: Sequence[int], tickers: Sequence[str] = ()) -> list[Bucket]:
    """Build a partition from interior edges.

    ``buckets_from_edges([69, 71, 73])`` gives ``<=69``, ``70-71``, ``72-73``,
    ``>=74``: the open bucket at each end plus one per gap. Edges must be
    strictly increasing.
    """
    if not edges:
        raise ValueError("need at least one edge")
    ordered = list(edges)
    if any(b <= a for a, b in zip(ordered, ordered[1:])):
        raise ValueError(f"edges must be strictly increasing: {edges}")

    out: list[Bucket] = [Bucket(None, ordered[0])]
    for a, b in zip(ordered, ordered[1:]):
        out.append(Bucket(a + 1, b))
    out.append(Bucket(ordered[-1] + 1, None))

    if tickers:
        if len(tickers) != len(out):
            raise ValueError(f"{len(tickers)} tickers for {len(out)} buckets")
        out = [Bucket(b.low, b.high, t) for b, t in zip(out, tickers)]
    return out


def parse_lead_hours(edges: Sequence[float], lead_hours: float) -> int:
    """Index of the lead-time bucket containing ``lead_hours``.

    Anything at or beyond the last edge falls in the final bucket, so a
    ten-day-out forecast is scored with the widest measured error rather than
    silently dropped.
    """
    if lead_hours < 0:
        raise ValueError(f"lead_hours must be non-negative: {lead_hours}")
    idx = int(np.searchsorted(np.asarray(edges, dtype=float), lead_hours, side="right") - 1)
    return max(0, min(idx, len(edges) - 2))


@dataclass
class _Fitted:
    """Parameters for one lead-time bucket."""

    n: int
    bias: float
    scale: float
    residuals: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))


class ErrorModel:
    """The distribution of (observed - forecast), fitted per lead time.

    Two families:

    ``gaussian``
        Bias and scale by maximum likelihood. Cheap, and the standard error of
        a bucket probability is available in closed form. Wrong in the tails.

    ``empirical``
        The ECDF of the residuals, linearly interpolated, with Gaussian tails
        beyond the observed range so that a far-out bucket gets a small
        probability rather than exactly zero. Needs more data and makes no
        shape assumption inside the observed range.

    The model is per-station by construction: pass one station's samples. A
    pooled fit across cities would average a coastal station's small errors
    into a continental one's large ones and be wrong in both directions.
    """

    def __init__(
        self,
        fits: dict[int, _Fitted],
        family: Literal["gaussian", "empirical"],
        lead_edges: Sequence[float],
        station: str = "",
    ) -> None:
        self._fits = fits
        self.family = family
        self.lead_edges = tuple(lead_edges)
        self.station = station

    # -- construction -------------------------------------------------------

    @classmethod
    def fit(
        cls,
        samples: Iterable[ErrorSample],
        family: Literal["gaussian", "empirical"] = "gaussian",
        lead_edges: Sequence[float] = LEAD_BUCKET_EDGES,
        min_samples: int = MIN_SAMPLES,
        station: str = "",
    ) -> "ErrorModel":
        """Fit one error distribution per lead-time bucket.

        Buckets with fewer than ``min_samples`` pairs are left unfitted, and
        asking one for a probability raises. Partial coverage is normal and
        useful: a model calibrated at one day and not at five can still trade
        the day-ahead market.
        """
        rows = list(samples)
        if not rows:
            raise ValueError("no samples")

        edges = tuple(float(e) for e in lead_edges)
        by_bucket: dict[int, list[float]] = {}
        for s in rows:
            by_bucket.setdefault(parse_lead_hours(edges, s.lead_hours), []).append(s.error)

        fits: dict[int, _Fitted] = {}
        for idx, errors in by_bucket.items():
            arr = np.asarray(errors, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size < min_samples:
                continue
            scale = float(arr.std(ddof=1))
            if scale <= 0:
                # Every forecast was exactly right, which in practice means the
                # pairs are duplicated rather than that the weather is solved.
                continue
            fits[idx] = _Fitted(
                n=int(arr.size),
                bias=float(arr.mean()),
                scale=scale,
                residuals=np.sort(arr),
            )

        if not fits:
            raise UncalibratedModel(
                f"no lead-time bucket reached {min_samples} samples "
                f"(largest had {max((len(v) for v in by_bucket.values()), default=0)})"
            )
        return cls(fits, family, edges, station)

    # -- introspection ------------------------------------------------------

    def calibrated_leads(self) -> list[tuple[float, float, int]]:
        """``(low_hours, high_hours, n)`` for each fitted bucket."""
        return [
            (self.lead_edges[i], self.lead_edges[i + 1], f.n)
            for i, f in sorted(self._fits.items())
        ]

    def _fit_for(self, lead_hours: float) -> _Fitted:
        idx = parse_lead_hours(self.lead_edges, lead_hours)
        fitted = self._fits.get(idx)
        if fitted is None:
            have = ", ".join(f"{a:g}-{b:g}h (n={n})" for a, b, n in self.calibrated_leads())
            raise UncalibratedModel(
                f"no fitted error distribution for a lead of {lead_hours:g}h; "
                f"calibrated ranges are: {have or 'none'}"
            )
        return fitted

    def summary(self, lead_hours: float) -> dict[str, float]:
        f = self._fit_for(lead_hours)
        return {"n": f.n, "bias_f": f.bias, "scale_f": f.scale}

    # -- the distribution ---------------------------------------------------

    def cdf(self, error_f: float, lead_hours: float) -> float:
        """P(observed - forecast <= error_f)."""
        f = self._fit_for(lead_hours)
        if self.family == "gaussian":
            return float(_norm_cdf((error_f - f.bias) / f.scale))
        return float(_empirical_cdf(error_f, f))

    def sf(self, error_f: float, lead_hours: float) -> float:
        """P(observed - forecast > error_f), computed without cancellation.

        Not ``1 - cdf``. In the right tail the CDF is 1 to every bit a float
        has, so the complement evaluates to exactly zero and a far bucket is
        priced at 0c — which invites an unbounded short on a contract that can
        still settle YES. Kept as its own path so the small number survives.
        """
        f = self._fit_for(lead_hours)
        if self.family == "gaussian":
            return float(_norm_sf((error_f - f.bias) / f.scale))
        return float(_empirical_sf(error_f, f))

    def probability(self, bucket: Bucket, forecast_f: float, lead_hours: float) -> float:
        """P(the reported integer high falls in ``bucket``).

        The forecast enters only through the bounds: the event is
        ``lo - forecast <= error < hi - forecast`` with ``lo``/``hi`` the
        half-degree bounds of the bucket.

        Buckets above the fitted centre are differenced through the survival
        function and buckets below it through the CDF, so that whichever tail
        the bucket sits in, the two terms being subtracted are both small.
        Doing it the other way loses the entire value of a far bucket to
        floating-point cancellation — silently, and only for the contracts
        where being wrong is least recoverable.
        """
        f = self._fit_for(lead_hours)
        lo, hi = bucket.continuous_bounds
        in_right_tail = lo != -math.inf and (lo - forecast_f) >= f.bias

        if in_right_tail:
            lower_sf = self.sf(lo - forecast_f, lead_hours)
            upper_sf = 0.0 if hi == math.inf else self.sf(hi - forecast_f, lead_hours)
            value = lower_sf - upper_sf
        else:
            upper = 1.0 if hi == math.inf else self.cdf(hi - forecast_f, lead_hours)
            lower = 0.0 if lo == -math.inf else self.cdf(lo - forecast_f, lead_hours)
            value = upper - lower
        return float(min(1.0, max(0.0, value)))

    def stderr_of_probability(
        self, bucket: Bucket, forecast_f: float, lead_hours: float
    ) -> float:
        """Standard error of `probability`, from the fitted parameters only.

        Delta method on the Gaussian family: the probability is a function of
        the fitted bias and scale, whose sampling variances are ``s^2/n`` and
        ``s^2/(2n)``. For the empirical family the residuals are resampled.

        This is estimation risk, not model risk. It does not know that the
        Gaussian tail is thin, and it will be smallest exactly where that
        matters most. Sizing on it alone over-bets the tails.
        """
        f = self._fit_for(lead_hours)
        lo, hi = bucket.continuous_bounds

        if self.family == "gaussian":
            z_hi = math.inf if hi == math.inf else (hi - forecast_f - f.bias) / f.scale
            z_lo = -math.inf if lo == -math.inf else (lo - forecast_f - f.bias) / f.scale
            phi_hi = 0.0 if math.isinf(z_hi) else _norm_pdf(z_hi)
            phi_lo = 0.0 if math.isinf(z_lo) else _norm_pdf(z_lo)
            d_bias = (phi_lo - phi_hi) / f.scale
            d_scale = (
                (0.0 if math.isinf(z_lo) else z_lo * phi_lo)
                - (0.0 if math.isinf(z_hi) else z_hi * phi_hi)
            ) / f.scale
            var = (d_bias ** 2) * (f.scale ** 2) / f.n
            var += (d_scale ** 2) * (f.scale ** 2) / (2 * f.n)
            return float(math.sqrt(max(var, 0.0)))

        p = self.probability(bucket, forecast_f, lead_hours)
        return float(math.sqrt(max(p * (1.0 - p), 0.0) / f.n))

    # -- persistence --------------------------------------------------------

    def to_json(self, path: str | Path) -> None:
        payload = {
            "family": self.family,
            "station": self.station,
            "lead_edges": list(self.lead_edges),
            "fits": {
                str(i): {
                    "n": f.n,
                    "bias": f.bias,
                    "scale": f.scale,
                    "residuals": [float(v) for v in f.residuals],
                }
                for i, f in self._fits.items()
            },
        }
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: str | Path) -> "ErrorModel":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        fits = {
            int(k): _Fitted(
                n=int(v["n"]),
                bias=float(v["bias"]),
                scale=float(v["scale"]),
                residuals=np.asarray(v.get("residuals", []), dtype=float),
            )
            for k, v in payload["fits"].items()
        }
        return cls(fits, payload["family"], payload["lead_edges"], payload.get("station", ""))


def bucket_probabilities(
    buckets: Sequence[Bucket],
    model: ErrorModel,
    forecast_f: float,
    lead_hours: float,
    require_partition: bool = True,
) -> list[float]:
    """Probability for every bucket in a family, normalised to sum to one.

    ``require_partition`` checks that the buckets tile the integers with no gap
    and no overlap before scoring them. This is worth enforcing rather than
    trusting: a family with a missing degree produces probabilities that sum to
    less than one, and the resulting fair values are all biased low by the same
    fraction — an error that looks exactly like finding an edge in every
    contract at once.

    Normalisation is applied only to remove floating-point drift, and a
    normaliser more than 1e-9 from unity on a valid partition is a bug in the
    distribution, not in the buckets, so it raises.
    """
    if not buckets:
        raise ValueError("no buckets")
    if require_partition:
        _check_partition(buckets)

    raw = [model.probability(b, forecast_f, lead_hours) for b in buckets]
    total = sum(raw)
    if total <= 0:
        raise ValueError("every bucket scored zero; forecast is far outside the family")
    if require_partition and abs(total - 1.0) > 1e-9:
        raise ValueError(
            f"a partition of buckets scored {total!r}, not 1.0 — the error "
            "distribution is not a proper distribution"
        )
    return [p / total for p in raw]


def _check_partition(buckets: Sequence[Bucket]) -> None:
    ordered = sorted(buckets, key=lambda b: (-math.inf if b.low is None else b.low))
    if ordered[0].low is not None or ordered[-1].high is not None:
        raise ValueError(
            "a temperature family must be open at both ends; got "
            f"low={ordered[0].low}, high={ordered[-1].high}"
        )
    for left, right in zip(ordered, ordered[1:]):
        if left.high is None or right.low is None:
            raise ValueError("only the first and last bucket may be open")
        if right.low != left.high + 1:
            gap = "gap" if right.low > left.high + 1 else "overlap"
            raise ValueError(
                f"{gap} between buckets ending {left.high} and starting {right.low}"
            )


# -- small numerics, kept local so the module has no scipy import ------------


def _norm_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _norm_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _empirical_cdf(error_f: float, f: _Fitted) -> float:
    """ECDF with Gaussian tails outside the observed range.

    Inside the range this is the sample distribution and assumes nothing. The
    tails are grafted on because a bucket beyond every residual ever seen has a
    small probability, not a zero one, and a zero would price a contract at
    0c and invite an unbounded position.
    """
    res = f.residuals
    if res.size == 0:
        return _norm_cdf((error_f - f.bias) / f.scale)
    if error_f < res[0]:
        # Match the ECDF's own value at the boundary so the curve is continuous.
        edge = 1.0 / (res.size + 1)
        return edge * _norm_cdf((error_f - f.bias) / f.scale) / max(
            _norm_cdf((res[0] - f.bias) / f.scale), 1e-12
        )
    if error_f > res[-1]:
        edge = 1.0 - 1.0 / (res.size + 1)
        tail = 1.0 - _norm_cdf((error_f - f.bias) / f.scale)
        ref = max(1.0 - _norm_cdf((res[-1] - f.bias) / f.scale), 1e-12)
        return 1.0 - (1.0 - edge) * tail / ref
    ranks = (np.arange(res.size) + 1.0) / (res.size + 1.0)
    return float(np.interp(error_f, res, ranks))


def _empirical_sf(error_f: float, f: _Fitted) -> float:
    """Survival function of the ECDF, with the same grafted Gaussian tails.

    The right-tail branch is written multiplicatively rather than as
    ``1 - cdf`` so that a value of order 1e-100 comes back as 1e-100.
    """
    res = f.residuals
    if res.size == 0:
        return _norm_sf((error_f - f.bias) / f.scale)
    if error_f > res[-1]:
        edge = 1.0 - 1.0 / (res.size + 1)
        tail = _norm_sf((error_f - f.bias) / f.scale)
        ref = max(_norm_sf((res[-1] - f.bias) / f.scale), 1e-300)
        return (1.0 - edge) * tail / ref
    return 1.0 - _empirical_cdf(error_f, f)
