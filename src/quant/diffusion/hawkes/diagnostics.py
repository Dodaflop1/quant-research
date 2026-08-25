"""Goodness-of-fit for a fitted point process, via the time-rescaling theorem.

The theorem: if the fitted conditional intensity is the true one, then the
compensator-transformed event times

    tau_k = Lambda(t_k) = integral of lambda(s) ds from 0 to t_k

have inter-arrival times that are independent and identically distributed
unit-rate exponential. So a Hawkes fit can be tested by transforming the data
through its own fitted intensity and asking whether what comes out is
indistinguishable from a Poisson process.

**Two tests, because the theorem makes two claims.** The KS test checks the
marginal distribution - that the rescaled gaps are unit exponential. It says
nothing about whether they are independent, and a badly misspecified kernel can
easily produce correctly-distributed gaps that remain autocorrelated. That
second claim needs a Ljung-Box test on the rescaled series. Reporting only the
KS test is the standard way a Hawkes fit passes validation it should have
failed, so both are computed here and both are reported.

A caveat that belongs in the write-up rather than a footnote: the parameters
being tested were estimated from the same data. That makes the KS p-value
optimistic, because the fit has already spent some of its freedom matching
these residuals. Treat a rejection as decisive and a pass as weak evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy import stats

from quant.common.db.schema import HawkesParameters, ResidualDiagnostics
from quant.diffusion.hawkes.model import _decay_states

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RescaledResiduals:
    """Rescaled inter-arrival times plus everything needed to plot them."""

    intervals: np.ndarray
    """Rescaled inter-arrival times; unit-rate exponential under the null."""

    uniforms: np.ndarray
    """``1 - exp(-interval)``; standard uniform under the null. Q-Q ready."""

    ks: ResidualDiagnostics
    """Kolmogorov-Smirnov test against the unit exponential."""

    ljung_box: ResidualDiagnostics
    """Ljung-Box test for autocorrelation in the rescaled series."""

    autocorrelations: np.ndarray
    """Sample autocorrelation of ``intervals`` at lags 1..h, for plotting."""

    @property
    def passes(self) -> bool:
        """True only when neither test rejects.

        Deliberately conjunctive. A fit that produces unit-exponential but
        autocorrelated residuals has not been validated, whatever the KS
        p-value says.
        """
        return not (self.ks.rejects_null or self.ljung_box.rejects_null)

    def report(self) -> str:
        verdict = "consistent with the fit" if self.passes else "REJECTS the fit"
        return (
            f"{self.ks.n_residuals} rescaled residuals: {verdict}\n"
            f"  KS vs Exp(1)   D={self.ks.statistic:.4f}  p={self.ks.p_value:.4f}\n"
            f"  Ljung-Box      Q={self.ljung_box.statistic:.2f}  "
            f"p={self.ljung_box.p_value:.4f}\n"
            f"  mean {self.intervals.mean():.4f} (1.0 under the null), "
            f"var {self.intervals.var():.4f} (1.0 under the null)"
        )


def rescale(
    times: Sequence[float] | np.ndarray,
    mu: float,
    alpha: float,
    beta: float,
) -> np.ndarray:
    """Rescaled inter-arrival times ``Lambda(t_k) - Lambda(t_{k-1})``.

    Computed by recursion rather than by evaluating the compensator at each
    event. Expanding

        Lambda(t_k) - Lambda(t_{k-1})
            = mu * dt + (alpha / beta) * (1 + A_{k-1}) * (1 - exp(-beta * dt))

    reuses the same ``A`` state the likelihood already maintains, so the whole
    residual series costs O(n) instead of O(n^2).
    """
    times = np.asarray(times, dtype=float)
    if len(times) == 0:
        return np.empty(0)

    states = _decay_states(times, beta)
    gaps = np.diff(times)

    intervals = np.empty(len(times), dtype=float)
    # The first event has no predecessor: Lambda(t_0) - Lambda(0) is just the
    # baseline, since no event has yet occurred to excite anything.
    intervals[0] = mu * times[0]
    if len(times) > 1:
        intervals[1:] = mu * gaps + (alpha / beta) * (1.0 + states[:-1]) * (
            1.0 - np.exp(-beta * gaps)
        )
    return intervals


def _ljung_box(x: np.ndarray, lags: int) -> tuple[float, float, np.ndarray]:
    """Ljung-Box Q statistic, its p-value, and the autocorrelations used."""
    n = len(x)
    centred = x - x.mean()
    denom = float(np.dot(centred, centred))
    if denom == 0:
        return 0.0, 1.0, np.zeros(lags)

    acf = np.array(
        [float(np.dot(centred[:-k], centred[k:])) / denom for k in range(1, lags + 1)]
    )
    q = n * (n + 2) * float(np.sum(acf**2 / (n - np.arange(1, lags + 1))))
    p_value = float(stats.chi2.sf(q, df=lags))
    return q, p_value, acf


def check_fit(
    times: Sequence[float] | np.ndarray,
    params: HawkesParameters,
    alpha_level: float = 0.05,
    lags: Optional[int] = None,
) -> RescaledResiduals:
    """Run the full time-rescaling diagnostic on a fitted model.

    Args:
        times: The same event times the model was fitted to.
        params: The fitted parameters.
        alpha_level: Significance level for both tests.
        lags: Ljung-Box lag count. Defaults to ``min(20, n // 5)``, the usual
            rule of thumb; too many lags dilutes power, too few miss slow
            structure.

    Returns:
        Both tests, the residuals, and the autocorrelations, in a form that can
        go straight into a reliability plot.
    """
    times = np.asarray(times, dtype=float)
    intervals = rescale(times, params.mu, params.alpha, params.beta)
    n = len(intervals)
    if n < 2:
        raise ValueError(f"need at least 2 residuals, got {n}")

    ks_stat, ks_p = stats.kstest(intervals, "expon", args=(0.0, 1.0))

    if lags is None:
        lags = max(1, min(20, n // 5))
    q_stat, q_p, acf = _ljung_box(intervals, lags)

    note = (
        "Parameters were estimated from these same events, so the KS p-value is "
        "optimistic; a rejection is decisive, a pass is weak evidence."
    )

    return RescaledResiduals(
        intervals=intervals,
        uniforms=1.0 - np.exp(-intervals),
        ks=ResidualDiagnostics(
            test_name="kolmogorov_smirnov_vs_unit_exponential",
            statistic=float(ks_stat),
            p_value=float(ks_p),
            n_residuals=n,
            alpha_level=alpha_level,
            notes=note,
        ),
        ljung_box=ResidualDiagnostics(
            test_name=f"ljung_box_lag{lags}",
            statistic=float(q_stat),
            p_value=float(q_p),
            n_residuals=n,
            alpha_level=alpha_level,
            notes=(
                "Tests independence of the rescaled series. The KS test cannot "
                "see autocorrelation, so a KS pass alone does not validate a fit."
            ),
        ),
        autocorrelations=acf,
    )
