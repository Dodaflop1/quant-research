"""Power-law (Omori) kernel for Hawkes estimation.

    phi(t) = n * eps * tau^eps / (t + tau)^(1 + eps),    integral_0^inf phi = n

A power law is not memoryless, so the O(n) recursion that makes the exponential
kernel tractable does not apply directly and a naive likelihood is O(n^2).
Instead the kernel is approximated as a **sum of exponentials** via the Gamma
integral

    (t + tau)^(-(1+eps)) = 1/Gamma(1+eps) * int_0^inf u^eps exp(-u(t+tau)) du

discretised on a geometric ``u`` grid. Each component then keeps its own
recursion, so the cost is O(n*M) with M components. Truncating the kernel would
be simpler and is wrong: the long tail is the entire object of study.

**The branching ratio is exact by construction.** After discretisation the
coefficients are rescaled so ``sum_j c_j / beta_j == n`` to machine precision,
which removes quadrature error from the one quantity that gets reported and
compared against the literature.

## The grid is fixed by the data, not by the parameters

This is the design point that a previous version got wrong, so it is stated
plainly: the beta grid is a property of the **observation window and sampling
resolution**, and is computed once from the full series. The kernel weights are
a property of ``(n, tau, eps)``.

Deriving the grid inside each call from whatever slice of data that call happens
to see means `compensator(t_end)` and `compensator(t_start)` build different
grids, so their difference is not an integral over the window. The same
parameters must never produce two different kernels.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

log = logging.getLogger(__name__)

_FLOOR = 1e-300
_PENALTY = 1e100
# Grid density. Measured against the closed form across tau in {0.1, 1, 10} and
# eps in {0.5, 1, 1.5, 3}, worst-case relative error:
#
#     M=25   1.4e-02      <- too coarse; the spec's "20-30" was wrong
#     M=40   5.2e-05      (for eps >= 1)
#     M=60   1.3e-08      (for eps >= 1)
#
# 40 buys four orders of magnitude over 25 for 1.6x the likelihood cost.
#
# For eps < 1 the error stays near 1e-2 whatever M is, because the failure is
# grid SPAN rather than density: a kernel with eps < 1 has infinite mean lag,
# so its mass extends below beta_min. And beta_min = 1/(10T) is an
# identifiability limit - decay slower than the observation window cannot be
# resolved from it - so this is a property of the problem, not a defect. The
# branching ratio is unaffected: it is exact by construction. Only the kernel
# SHAPE carries the ~1% error, and any eps < 1 result should say so.
_DEFAULT_M = 40


@dataclass(frozen=True)
class GridBounds:
    """Range of decay rates the data can distinguish.

    Computed **once** from the full series. Both ends are identifiability
    limits, not tuning knobs:

    * ``beta_min`` below ``1/(10T)`` is excitation that never decays inside the
      observation window, indistinguishable from a larger baseline.
    * ``beta_max`` above ``10 / smallest resolvable gap`` is excitation that has
      vanished before the next event could see it.

    The upper end uses a low **quantile** of gaps rather than the minimum: a
    single near-simultaneous pair drags the minimum to microseconds and inflates
    the ceiling by orders of magnitude. (Trade prints should be aggregated into
    order arrivals first — see ``hawkes.preprocess`` — but the bound should not
    depend on that having been done.)
    """

    beta_min: float
    beta_max: float

    @classmethod
    def from_series(cls, times: np.ndarray, T: float) -> "GridBounds":
        times = np.asarray(times, dtype=float)
        gaps = np.diff(times)
        positive = gaps[gaps > 0]
        if len(positive) >= 100:
            resolvable = float(np.quantile(positive, 0.01))
        elif len(positive):
            resolvable = float(positive.min())
        else:
            resolvable = T / max(len(times), 1)
        return cls(
            beta_min=1.0 / (10.0 * T),
            beta_max=10.0 / max(resolvable, T * 1e-12),
        )


class PowerLawKernel:
    """Sum-of-exponentials representation of the Omori kernel.

    Immutable once built. ``beta`` comes from the grid bounds, ``c`` from
    ``(n, tau, eps)``.
    """

    def __init__(
        self, n: float, tau: float, eps: float, bounds: GridBounds, m: int = _DEFAULT_M
    ):
        if n <= 0 or tau <= 0 or eps <= 0:
            raise ValueError("n, tau, eps must be positive")
        if m < 2:
            raise ValueError("need at least 2 grid points")

        self.n, self.tau, self.eps, self.m = n, tau, eps, m
        self.bounds = bounds

        log_beta = np.linspace(
            math.log(bounds.beta_min), math.log(bounds.beta_max), m
        )
        self.beta = np.exp(log_beta)

        # Quadrature weight for geometric spacing: du = u d(log u), so the
        # spacing factor is the ACTUAL grid step. A previous version used a
        # hardcoded log(r) unrelated to the linspace step; harmless only because
        # the rescaling below absorbs any constant, but it read as though it
        # meant something.
        d_log_beta = log_beta[1] - log_beta[0]
        log_w = (
            -gammaln(1.0 + eps)
            + (eps + 1.0) * log_beta
            - self.beta * tau
            + math.log(d_log_beta)
        )
        c = np.exp(math.log(n * eps) + eps * math.log(tau) + log_w)

        # Exact branching ratio. Removes discretisation error from the reported
        # quantity; asserted in the tests.
        total = float(np.sum(c / self.beta))
        self.c = c * (n / total) if total > 0 else c

    def phi(self, t: np.ndarray | float) -> np.ndarray:
        """The approximated kernel, for comparison against the closed form."""
        t = np.atleast_1d(np.asarray(t, dtype=float))
        return np.exp(-np.outer(t, self.beta)) @ self.c

    def phi_exact(self, t: np.ndarray | float) -> np.ndarray:
        """The closed-form Omori kernel the approximation targets."""
        t = np.atleast_1d(np.asarray(t, dtype=float))
        return self.n * self.eps * self.tau**self.eps / (t + self.tau) ** (1 + self.eps)

    def decay_states(self, times: np.ndarray) -> np.ndarray:
        """``A_j(i) = exp(-beta_j dt) (1 + A_j(i-1))`` for every component."""
        n = len(times)
        states = np.zeros((n, self.m), dtype=float)
        if n < 2:
            return states
        decays = np.exp(-np.outer(np.diff(times), self.beta))
        acc = np.zeros(self.m, dtype=float)
        for i in range(1, n):
            acc = decays[i - 1] * (1.0 + acc)
            states[i] = acc
        return states

    def intensity(self, times: np.ndarray, mu: float) -> np.ndarray:
        return mu + self.decay_states(times) @ self.c

    def compensator(self, times: np.ndarray, mu: float, upto: float) -> float:
        """``Lambda(upto)``, integrating every event strictly before it."""
        past = np.asarray(times, dtype=float)
        past = past[past < upto]
        if len(past) == 0:
            return mu * upto
        weights = self.c / self.beta
        decayed = np.exp(-np.outer(upto - past, self.beta)) @ weights
        # per event: sum_j (c_j/beta_j)(1 - exp(-beta_j (upto - t_i))) = n - decayed
        return mu * upto + float(np.sum(self.n - decayed))

    def log_likelihood(self, times: np.ndarray, mu: float, T: float) -> float:
        if mu <= 0:
            return -np.inf
        times = np.asarray(times, dtype=float)
        if len(times) == 0:
            return -mu * T
        intensities = self.intensity(times, mu)
        if np.any(intensities <= 0):
            return -np.inf
        return float(
            np.sum(np.log(np.maximum(intensities, _FLOOR)))
            - self.compensator(times, mu, T)
        )

    def log_likelihood_on_interval(
        self, times: np.ndarray, mu: float, t_start: float, t_end: float
    ) -> float:
        """Events in ``(t_start, t_end]``, conditioned on the FULL history.

        Only the summation range is restricted. Events before ``t_start`` still
        excite the process inside the window, so a held-out score that discards
        them is scoring a different model from the one that was fitted.

        Both compensator evaluations use this kernel's single grid, which is
        what makes the difference a valid integral.
        """
        if mu <= 0 or t_end <= t_start:
            return -np.inf
        times = np.asarray(times, dtype=float)
        if len(times) == 0:
            return -mu * (t_end - t_start)
        intensities = self.intensity(times, mu)
        window = (times > t_start) & (times <= t_end)
        if np.any(intensities[window] <= 0):
            return -np.inf
        return float(
            np.sum(np.log(np.maximum(intensities[window], _FLOOR)))
            - (
                self.compensator(times, mu, t_end)
                - self.compensator(times, mu, t_start)
            )
        )


@dataclass(frozen=True)
class PowerLawParameters:
    """Fitted parameters. Frozen, with derived quantities as properties."""

    mu: float
    n: float
    tau: float
    eps: float
    time_unit: Literal["second", "minute", "hour", "day"]
    log_likelihood: float
    n_events: int
    observation_window: float
    converged: bool
    std_errors: dict[str, float]
    m: int = _DEFAULT_M

    @property
    def branching_ratio(self) -> float:
        return self.n

    @property
    def is_stationary(self) -> bool:
        return self.n < 1.0

    @property
    def eps_is_identified(self) -> bool:
        """Whether ``eps`` is precise enough to say anything about.

        A recovery study at ~2,400 events returned eps of 0.98, 0.97, 1.22 and
        2.12 against a truth of 1.5 — a threefold spread — while ``n`` recovered
        to within 1.4%. ``tau`` and ``eps`` trade off against each other, so the
        combination governing the kernel integral is pinned while neither is
        individually. Since ``eps -> 0`` is exactly the regime where ``n`` is
        argued to approach 1 spuriously, a claim about ``eps`` needs its own
        standard error to mean anything.
        """
        se = self.std_errors.get("eps")
        return se is not None and se < 0.25 * self.eps

    def describe(self) -> str:
        flag = "" if self.eps_is_identified else "  [eps NOT identified]"
        return (
            f"n={self.n:.3f} tau={self.tau:.4g}s eps={self.eps:.3f} "
            f"mu={self.mu:.4g} converged={self.converged}{flag}"
        )


def _neg_ll(
    params: np.ndarray, times: np.ndarray, T: float, bounds: GridBounds, m: int
) -> float:
    mu, n, tau, eps = np.exp(params)
    if not np.all(np.isfinite([mu, n, tau, eps])):
        return _PENALTY
    try:
        kernel = PowerLawKernel(n=n, tau=tau, eps=eps, bounds=bounds, m=m)
    except ValueError:
        return _PENALTY
    value = -kernel.log_likelihood(times, mu, T)
    return _PENALTY if not math.isfinite(value) else value


def _param_bounds(times: np.ndarray, T: float) -> list[tuple[float, float]]:
    rate = len(times) / T if T > 0 else 1.0
    gaps = np.diff(times)
    positive = gaps[gaps > 0]
    typical = float(np.median(positive)) if len(positive) else T / max(len(times), 1)
    return [
        (math.log(1e-8 * rate), math.log(10.0 * rate)),   # mu
        (math.log(1e-8), math.log(20.0)),                 # n, well clear of 1
        (math.log(1e-6 * typical), math.log(10.0 * typical)),  # tau
        (math.log(1e-3), math.log(5.0)),                  # eps
    ]


def _at_bound(x: np.ndarray, bounds, tol: float = 1e-6) -> bool:
    return any(
        abs(v - lo) < tol * max(1.0, abs(lo)) or abs(v - hi) < tol * max(1.0, abs(hi))
        for v, (lo, hi) in zip(x, bounds)
    )


def _starts(times: np.ndarray, T: float) -> list[np.ndarray]:
    rate = len(times) / T if T > 0 else 1.0
    gaps = np.diff(times)
    positive = gaps[gaps > 0]
    typical = float(np.median(positive)) if len(positive) else T / max(len(times), 1)
    return [
        np.log([max(rate * (1.0 - n0), 1e-9), n0, ts * typical, e0])
        for n0 in (0.2, 0.5, 0.8)
        for ts in (0.1, 1.0, 10.0)
        for e0 in (0.5, 1.5)
    ]


def _numeric_hessian(f, x: np.ndarray, step: float = 1e-4) -> np.ndarray:
    k = len(x)
    hess = np.zeros((k, k))
    h = np.maximum(np.abs(x) * step, step)
    for i in range(k):
        for j in range(i, k):
            ei, ej = np.zeros(k), np.zeros(k)
            ei[i], ej[j] = h[i], h[j]
            hess[i, j] = hess[j, i] = (
                f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)
            ) / (4.0 * h[i] * h[j])
    return hess


def _standard_errors(f, log_params: np.ndarray) -> dict[str, float]:
    try:
        cov = np.linalg.inv(_numeric_hessian(f, log_params))
        var = np.diag(cov)
        if np.any(var <= 0) or not np.all(np.isfinite(var)):
            log.warning("non-positive variance in the Hessian; omitting std errors")
            return {}
        natural = np.sqrt(var) * np.exp(log_params)
        return dict(zip(("mu", "n", "tau", "eps"), (float(v) for v in natural)))
    except np.linalg.LinAlgError:
        log.warning("singular Hessian; omitting std errors")
        return {}


def fit_power_law(
    times: Sequence[float] | np.ndarray,
    T: Optional[float] = None,
    time_unit: Literal["second", "minute", "hour", "day"] = "second",
    compute_std_errors: bool = True,
    m: int = _DEFAULT_M,
) -> PowerLawParameters:
    """Fit an Omori-kernel Hawkes process by maximum likelihood.

    The grid bounds are derived once from the series and held fixed across the
    whole optimisation, so every candidate parameter set is scored against the
    same discretisation.
    """
    times = np.asarray(times, dtype=float)
    if len(times) < 2:
        raise ValueError(f"need at least 2 events to fit, got {len(times)}")
    if np.any(np.diff(times) < 0):
        raise ValueError("times must be sorted ascending")
    if times[0] < 0:
        raise ValueError("times must be measured from the start of the window")

    if T is None:
        T = float(times[-1])
        log.warning(
            "no observation window given; using the last event time (%.3f), "
            "which biases the rate upward slightly",
            T,
        )
    if T < times[-1]:
        raise ValueError(f"window T={T} ends before the last event {times[-1]}")

    grid = GridBounds.from_series(times, T)
    bounds = _param_bounds(times, T)
    lo = [b[0] for b in bounds]
    hi = [b[1] for b in bounds]

    best = None
    for start in _starts(times, T):
        result = minimize(
            _neg_ll,
            np.clip(start, lo, hi),
            args=(times, T, grid, m),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 5000, "ftol": 1e-12},
        )
        if not np.isfinite(result.fun) or result.fun >= _PENALTY:
            continue
        if best is None or result.fun < best.fun:
            best = result

    if best is None:
        raise RuntimeError("every starting point diverged; check the input times")

    mu, n, tau, eps = (float(v) for v in np.exp(best.x))
    on_bound = _at_bound(best.x, bounds)
    if on_bound:
        log.warning(
            "fit rests on a parameter bound (mu=%.4g n=%.4g tau=%.4g eps=%.4g). "
            "Reported as not converged: a boundary solution has no interior "
            "optimum and its standard errors would be meaningless.",
            mu, n, tau, eps,
        )

    std_errors = (
        _standard_errors(lambda p: _neg_ll(p, times, T, grid, m), best.x)
        if compute_std_errors and not on_bound
        else {}
    )

    return PowerLawParameters(
        mu=mu,
        n=n,
        tau=tau,
        eps=eps,
        time_unit=time_unit,
        log_likelihood=float(-best.fun),
        n_events=len(times),
        observation_window=float(T),
        converged=bool(best.success) and not on_bound,
        std_errors=std_errors,
        m=m,
    )


def simulate_power_law(
    mu: float,
    n: float,
    tau: float,
    eps: float,
    T: float,
    seed: Optional[int] = None,
    m: int = _DEFAULT_M,
    max_events: int = 500_000,
) -> np.ndarray:
    """Simulate by Ogata thinning against the multi-exponential representation.

    Needed for the validation studies: an estimator that cannot recover
    parameters from its own simulator should not be trusted on anything else.
    The grid here spans well past ``1/tau`` so short-lag behaviour is
    represented rather than truncated away.
    """
    if n >= 1.0:
        raise ValueError(f"branching ratio {n} >= 1 is explosive")
    bounds = GridBounds(beta_min=1.0 / (10.0 * T), beta_max=100.0 / tau)
    kernel = PowerLawKernel(n=n, tau=tau, eps=eps, bounds=bounds, m=m)

    rng = np.random.default_rng(seed)
    out: list[float] = []
    t = 0.0
    acc = np.zeros(m)  # sum_i exp(-beta (t - t_i)), maintained incrementally
    while True:
        # The intensity only decays between events, so its value now bounds it
        # everywhere until the next accepted event. That is what makes the
        # acceptance step exact rather than approximate.
        ceiling = mu + float(acc @ kernel.c)
        if ceiling <= 0:
            break
        dt = rng.exponential(1.0 / ceiling)
        t += dt
        if t >= T:
            break
        acc = acc * np.exp(-kernel.beta * dt)
        if rng.uniform(0.0, ceiling) <= mu + float(acc @ kernel.c):
            out.append(t)
            acc = acc + 1.0
            if len(out) > max_events:
                raise ValueError(f"simulation exceeded {max_events} events")
    return np.asarray(out, dtype=float)
