"""Separating a time-varying baseline from genuine self-excitation.

This module exists because of a measured result, not a theoretical worry. On
simulated arrivals with **zero** self-excitation - an inhomogeneous Poisson
process whose rate varies sinusoidally - a constant-baseline Hawkes fit reports
a branching ratio of **0.79**, and the residual diagnostics reject it only 12%
of the time. Published order-flow branching ratios sit at 0.8-0.9. A fit to raw
Kalshi trade times inherits that bias and is not reportable.

The correction used here is a **seasonal time change**, which is the standard
treatment and is exact rather than approximate.

Write the intensity as a periodic baseline times a self-exciting part:

    lambda(t) = s(t) * [ mu + sum_{t_i < t} alpha * exp(-beta * (tau(t) - tau(t_i))) ]

where ``s`` is a periodic seasonality factor normalised to mean 1, and

    tau(t) = integral_0^t s(u) du

is the *operational time* it induces. Transforming the event times through
``tau`` makes the baseline constant by construction, so an ordinary
constant-baseline Hawkes fit on the transformed times is correctly specified
with respect to seasonality.

Two properties make this worth doing rather than the alternatives:

* **The branching ratio is invariant under the time change.** ``n`` counts
  expected direct offspring per event, which is a property of the branching
  structure and not of the clock. A monotone reparameterisation of time cannot
  change how many children an event has. So ``n`` fitted in operational time is
  directly comparable to ``n`` fitted anywhere else - which is exactly the
  quantity being compared against the literature.
* **``beta`` is not invariant.** It is a rate, expressed per unit of operational
  time. :func:`SeasonalProfile.mean_rate_scale` gives the factor needed to talk
  about half-lives in wall-clock seconds again, and any reported half-life must
  state which clock it is on.

The estimator for ``s`` is deliberately nonparametric - a binned rate by phase
within the period. Fitting a parametric seasonal shape would mean choosing a
functional form for exactly the thing that is being controlled for, and getting
that form wrong reintroduces the bias it was meant to remove.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86_400.0

# Below this many events per bin the binned rate is dominated by counting noise,
# and "estimating" seasonality from it manufactures structure rather than
# removing it. See SeasonalProfile.estimate.
MIN_EVENTS_PER_BIN = 20


@dataclass(frozen=True)
class SeasonalProfile:
    """A piecewise-constant periodic intensity multiplier, normalised to mean 1.

    ``factors[k]`` is the relative arrival rate during phase bin ``k`` of the
    period. A flat profile (all ones) means no seasonality was estimated, which
    is reported rather than silently applied.
    """

    factors: np.ndarray
    period: float
    """Period in the same time unit as the event times."""

    n_events: int
    is_flat: bool
    """True when estimation was declined - too few events to bin reliably."""

    reason: str = ""

    @property
    def n_bins(self) -> int:
        return len(self.factors)

    @property
    def peak_to_trough(self) -> float:
        """Ratio of busiest to quietest bin. 1.0 means no seasonality at all.

        This is the number that says whether the correction mattered. A profile
        near 1.0 means the naive and deseasonalised fits should agree, and a
        large ratio means they should not.
        """
        lo = float(self.factors.min())
        return float(self.factors.max()) / lo if lo > 0 else float("inf")

    def factor_at(self, times: np.ndarray) -> np.ndarray:
        """The seasonality multiplier in force at each time."""
        idx = self._bin_index(np.asarray(times, dtype=float))
        return self.factors[idx]

    def _bin_index(self, times: np.ndarray) -> np.ndarray:
        phase = np.mod(times, self.period) / self.period
        idx = (phase * self.n_bins).astype(int)
        # Guard the closed upper edge: phase exactly 1.0 would index past the end.
        return np.clip(idx, 0, self.n_bins - 1)

    @property
    def mean_rate_scale(self) -> float:
        """Operational time units per wall-clock unit, on average.

        Always 1.0 by construction, since the profile is normalised to mean 1.
        Exposed so that code converting a half-life back to wall clock states
        the conversion explicitly rather than assuming it.
        """
        return float(self.factors.mean())

    @classmethod
    def estimate(
        cls,
        times: Sequence[float] | np.ndarray,
        T: float,
        period: float = SECONDS_PER_DAY,
        n_bins: int = 24,
        min_events_per_bin: int = MIN_EVENTS_PER_BIN,
    ) -> "SeasonalProfile":
        """Estimate a periodic profile by binned rate.

        Exposure per bin is computed exactly rather than assumed uniform: an
        observation window that is not a whole number of periods gives some
        phase bins more wall-clock time than others, and dividing by a uniform
        exposure would read that asymmetry as seasonality.

        Declines to estimate - returning a flat profile with a reason - when
        there are too few events per bin. Binning 200 events into 24 bins gives
        8 per bin, where Poisson noise alone produces a peak-to-trough ratio
        near 3, and "correcting" for that fabricates structure.
        """
        times = np.asarray(times, dtype=float)
        n = len(times)

        if n < min_events_per_bin * n_bins:
            return cls(
                factors=np.ones(n_bins),
                period=period,
                n_events=n,
                is_flat=True,
                reason=(
                    f"{n} events over {n_bins} bins is under {min_events_per_bin}"
                    " per bin; binned rates would be counting noise"
                ),
            )

        counts = np.bincount(
            cls(np.ones(n_bins), period, n, True)._bin_index(times),
            minlength=n_bins,
        ).astype(float)

        exposure = _phase_exposure(T, period, n_bins)
        if np.any(exposure <= 0):
            return cls(
                factors=np.ones(n_bins),
                period=period,
                n_events=n,
                is_flat=True,
                reason="observation window does not cover every phase bin",
            )

        rates = counts / exposure
        mean_rate = rates.mean()
        if mean_rate <= 0:
            return cls(
                factors=np.ones(n_bins),
                period=period,
                n_events=n,
                is_flat=True,
                reason="zero mean rate",
            )

        factors = rates / mean_rate

        # A bin with no events would send the intensity to zero there and make
        # the time change degenerate. Floor it at a small fraction rather than
        # dropping the bin, and say so.
        floor = 1e-3
        if np.any(factors < floor):
            log.warning(
                "%d phase bin(s) had near-zero rate; floored at %.0e",
                int(np.sum(factors < floor)),
                floor,
            )
            factors = np.maximum(factors, floor)
            factors = factors / factors.mean()

        return cls(
            factors=factors,
            period=period,
            n_events=n,
            is_flat=False,
            reason="",
        )

    def to_operational_time(
        self, times: Sequence[float] | np.ndarray
    ) -> np.ndarray:
        """Map wall-clock times to operational time, ``tau(t) = int_0^t s(u) du``.

        Exact for a piecewise-constant profile: whole bins contribute their full
        width times their factor, and the partial bin containing ``t``
        contributes linearly.
        """
        times = np.asarray(times, dtype=float)
        if self.is_flat:
            return times.copy()

        bin_width = self.period / self.n_bins
        # Integral over one complete period, and the running integral at each
        # bin boundary within a period.
        per_period = float(self.factors.sum()) * bin_width
        cum_at_edge = np.concatenate([[0.0], np.cumsum(self.factors) * bin_width])

        whole_periods = np.floor(times / self.period)
        remainder = times - whole_periods * self.period
        idx = np.clip((remainder / bin_width).astype(int), 0, self.n_bins - 1)
        into_bin = remainder - idx * bin_width

        return (
            whole_periods * per_period
            + cum_at_edge[idx]
            + into_bin * self.factors[idx]
        )

    def describe(self) -> str:
        if self.is_flat:
            return f"seasonality: NOT APPLIED ({self.reason})"
        return (
            f"seasonality: {self.n_bins} bins over {self.period / 3600:.0f}h, "
            f"peak/trough {self.peak_to_trough:.2f}, "
            f"busiest bin {int(np.argmax(self.factors))}, "
            f"quietest bin {int(np.argmin(self.factors))}"
        )


def _phase_exposure(T: float, period: float, n_bins: int) -> np.ndarray:
    """Wall-clock time spent in each phase bin over ``[0, T]``.

    Computed exactly. A window of 2.5 days gives the first half of the day's
    bins three visits and the second half two, and treating exposure as uniform
    would read that purely calendrical asymmetry as a 50% seasonal effect.
    """
    bin_width = period / n_bins
    exposure = np.zeros(n_bins)

    full_periods = int(T // period)
    exposure += full_periods * bin_width

    remainder = T - full_periods * period
    if remainder > 0:
        full_bins = int(remainder // bin_width)
        exposure[:full_bins] += bin_width
        if full_bins < n_bins:
            exposure[full_bins] += remainder - full_bins * bin_width
    return exposure


def deseasonalise(
    times: Sequence[float] | np.ndarray,
    T: float,
    period: float = SECONDS_PER_DAY,
    n_bins: int = 24,
    profile: Optional[SeasonalProfile] = None,
) -> tuple[np.ndarray, float, SeasonalProfile]:
    """Transform event times into operational time.

    Returns ``(operational_times, operational_T, profile)``. Fitting a
    constant-baseline Hawkes process to the returned times is correctly
    specified with respect to seasonality; the resulting branching ratio is
    directly comparable to one fitted on any other clock, while ``beta`` and any
    half-life derived from it are in operational units.
    """
    times = np.asarray(times, dtype=float)
    if profile is None:
        profile = SeasonalProfile.estimate(times, T, period=period, n_bins=n_bins)
    return (
        profile.to_operational_time(times),
        float(profile.to_operational_time(np.array([T]))[0]),
        profile,
    )
