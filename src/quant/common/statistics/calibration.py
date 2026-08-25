"""Calibration metrics for probabilistic forecasts.

A fair-value model that says 30% must be right 30% of the time. Everything here
measures that, and separates it from the different question of whether the
forecast is *useful*.

The distinction matters and is the reason for the decomposition below. A
forecaster who always predicts the base rate is perfectly calibrated and
completely useless. One who is confidently wrong is useful in reverse. Only the
two numbers together say anything.

What is here
------------
- :func:`brier_score` — mean squared error of a probability.
- :func:`brier_decomposition` — Murphy's split into reliability, resolution and
  uncertainty, **with the binning residual reported rather than assumed zero.**
- :func:`log_loss` — the proper scoring rule that punishes confident errors
  hardest, which is the failure mode that matters when sizing a position.
- :func:`reliability_curve` — observed frequency against forecast probability,
  with Wilson intervals so a bin holding four outcomes cannot masquerade as
  evidence.
- :func:`spiegelhalter_z` — an actual hypothesis test for calibration, rather
  than eyeballing a diagram.
- :func:`skill_score` — improvement over a named reference forecast. A Brier
  score alone has no scale; against climatology it does.

On the decomposition identity
-----------------------------
The textbook statement is ``BS = REL - RES + UNC``. That is **exact only when
every forecast inside a bin is identical** — which is true for a forecaster
emitting finitely many distinct values, and false for anything continuous.
Binning a continuous forecast introduces a within-bin variance term that the
three components do not account for.

Most implementations quietly drop it. This one computes it and returns it as
``residual``, because a decomposition that does not add up is a decomposition
that is hiding something. On real data the residual is usually small; if it is
not, the binning is too coarse and the reliability figure cannot be trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

import numpy as np

__all__ = [
    "BrierDecomposition",
    "ReliabilityBin",
    "brier_score",
    "brier_decomposition",
    "log_loss",
    "reliability_curve",
    "skill_score",
    "spiegelhalter_z",
    "wilson_interval",
]


def _clean(probabilities: Sequence[float] | np.ndarray,
           outcomes: Sequence[int] | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Validate and coerce. Refuses silently-wrong input rather than scoring it."""
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if p.shape != y.shape:
        raise ValueError(f"probabilities and outcomes differ in shape: {p.shape} vs {y.shape}")
    if p.ndim != 1:
        raise ValueError("expected one-dimensional arrays")
    if p.size == 0:
        raise ValueError("no forecasts to score")
    if not np.all(np.isfinite(p)):
        raise ValueError("probabilities contain non-finite values")
    if np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError("probabilities must lie in [0, 1]; are these cents rather than odds?")
    if not np.all(np.isin(y, (0.0, 1.0))):
        raise ValueError("outcomes must be 0 or 1")
    return p, y


def brier_score(probabilities: Sequence[float] | np.ndarray,
                outcomes: Sequence[int] | np.ndarray) -> float:
    """Mean squared error of a probabilistic forecast. Lower is better.

    Bounded in [0, 1]. A coin-flip forecaster on a balanced problem scores 0.25;
    always predicting the base rate scores ``base * (1 - base)``.
    """
    p, y = _clean(probabilities, outcomes)
    return float(np.mean((p - y) ** 2))


def log_loss(probabilities: Sequence[float] | np.ndarray,
             outcomes: Sequence[int] | np.ndarray,
             clip: float = 1e-15) -> float:
    """Negative log-likelihood per forecast. Lower is better.

    Unlike Brier, this is unbounded: one confident miss can dominate the whole
    sample. That is the point — when the score feeds position sizing, a forecast
    of 0.999 that resolves false should hurt far more than four times as much as
    one of 0.5.

    ``clip`` bounds the probabilities away from 0 and 1 so a single certain miss
    returns a large number rather than infinity. Report it; do not tune it.
    """
    p, y = _clean(probabilities, outcomes)
    p = np.clip(p, clip, 1.0 - clip)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def skill_score(score: float, reference: float) -> float:
    """Fractional improvement over a reference score. 1 is perfect, 0 is no better.

    Negative means the forecast is worse than the thing it is being compared
    with, which is the result worth reporting loudest. The reference should be
    named wherever this is quoted — skill against climatology and skill against
    the market's own price are very different claims.
    """
    if reference == 0:
        raise ValueError("reference score of zero has no skill scale")
    return float(1.0 - score / reference)


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Used rather than the normal approximation because reliability bins are
    routinely small and land near 0 or 1, where ``p ± z*sqrt(p(1-p)/n)`` gives
    intervals that extend outside [0, 1] and cover far less than advertised.
    """
    if trials <= 0:
        return (float("nan"), float("nan"))
    phat = successes / trials
    denom = 1.0 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denom
    half = z * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class ReliabilityBin:
    """One point of a reliability diagram."""

    lower: float
    upper: float
    n: int
    mean_forecast: float
    observed_frequency: float
    ci_low: float
    ci_high: float

    @property
    def gap(self) -> float:
        """Observed minus forecast. Positive means the forecast was too low."""
        return self.observed_frequency - self.mean_forecast

    @property
    def is_significant(self) -> bool:
        """Whether the forecast lies outside the bin's confidence interval.

        A bin whose interval spans the diagonal is not evidence of miscalibration
        however far its point sits from it.
        """
        return not (self.ci_low <= self.mean_forecast <= self.ci_high)


def _edges(bins: int | Sequence[float]) -> np.ndarray:
    if isinstance(bins, int):
        if bins < 2:
            raise ValueError("need at least 2 bins")
        return np.linspace(0.0, 1.0, bins + 1)
    edges = np.asarray(bins, dtype=float)
    if edges.ndim != 1 or edges.size < 3:
        raise ValueError("explicit bin edges need at least 3 values")
    if not np.all(np.diff(edges) > 0):
        raise ValueError("bin edges must increase")
    return edges


def _assign(p: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Bin index per forecast, with the top edge closed so p = 1.0 is included."""
    idx = np.digitize(p, edges[1:-1], right=False)
    return np.clip(idx, 0, len(edges) - 2)


def reliability_curve(probabilities: Sequence[float] | np.ndarray,
                      outcomes: Sequence[int] | np.ndarray,
                      bins: int | Sequence[float] = 10,
                      min_count: int = 1) -> list[ReliabilityBin]:
    """Observed frequency against forecast probability, bin by bin.

    Bins holding fewer than ``min_count`` forecasts are dropped rather than
    plotted, because a bin of three is noise that looks like a finding on a
    diagram.
    """
    p, y = _clean(probabilities, outcomes)
    edges = _edges(bins)
    idx = _assign(p, edges)

    out: list[ReliabilityBin] = []
    for k in range(len(edges) - 1):
        mask = idx == k
        n = int(mask.sum())
        if n < max(1, min_count):
            continue
        hits = int(y[mask].sum())
        lo, hi = wilson_interval(hits, n)
        out.append(ReliabilityBin(
            lower=float(edges[k]), upper=float(edges[k + 1]), n=n,
            mean_forecast=float(p[mask].mean()),
            observed_frequency=hits / n, ci_low=lo, ci_high=hi,
        ))
    return out


@dataclass(frozen=True)
class BrierDecomposition:
    """Murphy's decomposition, with the binning error made visible."""

    brier: float
    reliability: float
    """Mean squared gap between forecast and observed frequency. **Lower is
    better** — this is the calibration term."""

    resolution: float
    """How far the binned outcome rates move away from the base rate. **Higher
    is better** — this is the discrimination term, and it is what a
    base-rate-parroting forecaster has none of."""

    uncertainty: float
    """``base_rate * (1 - base_rate)``. A property of the events, not the
    forecaster: the Brier score of always predicting the base rate."""

    residual: float
    """``brier - (reliability - resolution + uncertainty)``.

    Zero only when every forecast within a bin is identical. Non-zero is the
    within-bin variance the three-term identity does not carry, and a large
    value means the bins are too coarse to trust the reliability figure."""

    base_rate: float
    n: int
    n_bins_used: int

    @property
    def skill_vs_climatology(self) -> float:
        """``(resolution - reliability) / uncertainty``.

        Positive means the forecast beats always predicting the base rate.
        Identical to the Brier skill score against climatology, up to the
        residual."""
        if self.uncertainty == 0:
            return float("nan")
        return (self.resolution - self.reliability) / self.uncertainty

    def describe(self) -> str:
        flag = "" if abs(self.residual) < 0.005 else "   <-- BINNING RESIDUAL IS LARGE"
        return (
            f"Brier {self.brier:.4f} over {self.n:,} forecasts, base rate "
            f"{self.base_rate:.3f}\n"
            f"  reliability  {self.reliability:.4f}  (lower better; 0 is perfect calibration)\n"
            f"  resolution   {self.resolution:.4f}  (higher better; 0 is no discrimination)\n"
            f"  uncertainty  {self.uncertainty:.4f}  (fixed by the events)\n"
            f"  residual     {self.residual:+.4f}  (binning error){flag}\n"
            f"  skill vs climatology {self.skill_vs_climatology:+.3f}"
        )


def brier_decomposition(probabilities: Sequence[float] | np.ndarray,
                        outcomes: Sequence[int] | np.ndarray,
                        bins: int | Sequence[float] = 10) -> BrierDecomposition:
    """Split a Brier score into calibration, discrimination and irreducible parts.

    Reliability and resolution answer different questions and a single Brier
    score confounds them. A forecaster who always says 0.5 on a balanced problem
    has perfect reliability and zero resolution, and scores identically to one
    who is genuinely informative but badly calibrated.
    """
    p, y = _clean(probabilities, outcomes)
    edges = _edges(bins)
    idx = _assign(p, edges)
    n = p.size
    base = float(y.mean())

    reliability = resolution = 0.0
    used = 0
    for k in range(len(edges) - 1):
        mask = idx == k
        nk = int(mask.sum())
        if nk == 0:
            continue
        used += 1
        pk = float(p[mask].mean())
        ok = float(y[mask].mean())
        reliability += nk * (pk - ok) ** 2
        resolution += nk * (ok - base) ** 2
    reliability /= n
    resolution /= n
    uncertainty = base * (1.0 - base)
    bs = float(np.mean((p - y) ** 2))

    return BrierDecomposition(
        brier=bs, reliability=reliability, resolution=resolution,
        uncertainty=uncertainty,
        residual=bs - (reliability - resolution + uncertainty),
        base_rate=base, n=n, n_bins_used=used,
    )


def spiegelhalter_z(probabilities: Sequence[float] | np.ndarray,
                    outcomes: Sequence[int] | np.ndarray) -> tuple[float, float]:
    """Spiegelhalter's z-test for calibration. Returns ``(z, two_sided_p)``.

    Under the null that the forecasts are calibrated, the Brier score has a
    known mean and variance, and

        ``Z = sum (y - p)(1 - 2p) / sqrt(sum (1 - 2p)^2 p (1 - p))``

    is standard normal. A reliability diagram is a picture; this is a test, and
    it needs no binning choice, which is the part of a diagram most open to
    being tuned until it looks good.

    Interpretation: |z| above ~2 rejects calibration at 5%. The *sign* carries
    the direction — positive means outcomes came in above forecasts, so the
    model was systematically underconfident.

    Note it has no power against a forecaster who is calibrated but useless;
    pair it with the resolution term.
    """
    p, y = _clean(probabilities, outcomes)
    weight = 1.0 - 2.0 * p
    numerator = float(np.sum((y - p) * weight))
    variance = float(np.sum(weight ** 2 * p * (1.0 - p)))
    if variance <= 0:
        # Every forecast is exactly 0.5, where the statistic has no leverage.
        return (float("nan"), float("nan"))
    z = numerator / math.sqrt(variance)
    p_value = math.erfc(abs(z) / math.sqrt(2.0))
    return (z, p_value)
