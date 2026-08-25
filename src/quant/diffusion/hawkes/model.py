"""Maximum-likelihood estimation for the exponential-kernel Hawkes process.

The conditional intensity is

    lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

and the log-likelihood on ``[0, T]`` for events ``t_1 < ... < t_n`` is

    log L = -mu * T
            - (alpha / beta) * sum_i (1 - exp(-beta * (T - t_i)))
            + sum_i log(mu + alpha * A_i)

where ``A_i = sum_{j < i} exp(-beta * (t_i - t_j))``.

Written naively that sum is O(n^2), which is the difference between a fit that
takes a second and one that takes an hour on a few hundred thousand trades. The
exponential kernel admits the recursion

    A_1 = 0,    A_i = exp(-beta * (t_i - t_{i-1})) * (1 + A_{i-1})

which makes the whole likelihood O(n). That recursion is the only reason the
exponential kernel is the standard choice; a power-law kernel fits reflexivity
data better but costs the recursion.

Two deliberate choices about what is *not* enforced:

* **Stationarity is not constrained.** A fitted branching ratio at or above one
  is reported as-is. It is the single most informative diagnostic the fit
  produces - usually about a misspecified kernel or a window spanning a regime
  change - and constraining it away would replace a finding with a boundary
  solution that looks like a result.
* **Multiple starts, not one.** The Hawkes likelihood is multimodal in
  ``beta``, and a single start lands in a local optimum often enough that a
  one-start fit is not trustworthy. Starts are spread over decades of timescale.
"""

from __future__ import annotations

import logging
import math
from typing import Literal, Optional, Sequence

import numpy as np
from scipy.optimize import minimize

from quant.common.db.schema import HawkesParameters

log = logging.getLogger(__name__)

# Guards the log() in the likelihood. An intensity can legitimately approach
# zero between distant events; it can never be non-positive.
_FLOOR = 1e-300

# Returned for infeasible parameters. Large enough never to win, small enough
# that L-BFGS-B's finite-difference gradient stays finite - 1e300 produces
# inf-inf in the difference quotient and a NaN gradient, which stalls the
# line search instead of turning it around.
_PENALTY = 1e100


def _decay_states(times: np.ndarray, beta: float) -> np.ndarray:
    """The recursion ``A_i = exp(-beta * dt) * (1 + A_{i-1})``, vectorised head."""
    n = len(times)
    states = np.empty(n, dtype=float)
    states[0] = 0.0
    if n == 1:
        return states
    decays = np.exp(-beta * np.diff(times))
    acc = 0.0
    for i in range(1, n):
        acc = decays[i - 1] * (1.0 + acc)
        states[i] = acc
    return states


def log_likelihood(
    times: np.ndarray, mu: float, alpha: float, beta: float, T: float
) -> float:
    """Exact log-likelihood of an exponential-kernel Hawkes process."""
    if mu <= 0 or alpha <= 0 or beta <= 0:
        return -np.inf
    n = len(times)
    if n == 0:
        return -mu * T

    states = _decay_states(times, beta)
    intensities = mu + alpha * states
    if np.any(intensities <= 0):
        return -np.inf

    compensator = mu * T + (alpha / beta) * np.sum(
        1.0 - np.exp(-beta * (T - times))
    )
    return float(np.sum(np.log(np.maximum(intensities, _FLOOR))) - compensator)


def compensator(
    times: np.ndarray, mu: float, alpha: float, beta: float, upto: float
) -> float:
    """Integrated intensity ``Lambda(upto)``, used by the diagnostics."""
    past = times[times < upto]
    return float(
        mu * upto + (alpha / beta) * np.sum(1.0 - np.exp(-beta * (upto - past)))
    )


def _neg_ll_log_params(params: np.ndarray, times: np.ndarray, T: float) -> float:
    """Negative log-likelihood in log-parameter space.

    Optimising ``log mu, log alpha, log beta`` keeps every parameter positive
    without a constrained solver, and rescales the problem so that beta - which
    ranges over orders of magnitude - is not fighting mu for step size.
    """
    mu, alpha, beta = np.exp(params)
    if not np.all(np.isfinite([mu, alpha, beta])):
        return _PENALTY
    value = -log_likelihood(times, mu, alpha, beta, T)
    return _PENALTY if not math.isfinite(value) else value


def _bounds(times: np.ndarray, T: float) -> list[tuple[float, float]]:
    """Finite box constraints on the log-parameters.

    Without these, L-BFGS-B walks into the region where ``exp(log_beta)``
    overflows, the objective returns a constant penalty, the gradient there is
    exactly zero, and the optimiser reports *success* at a parameter value of
    order 1e190. Point estimates then look plausible in aggregate while the
    variance is infinite - which is how a validation study catches a bug that a
    single well-behaved fit hides. The 20,000-event sample this estimator was
    first tried on converged perfectly; the failure only appeared at 2,000.

    The bounds are not tuning knobs. Each marks a genuine edge of
    identifiability:

    * ``beta`` far below ``1 / T`` means excitation that never decays within the
      observation window, which is indistinguishable from a larger baseline.
    * ``beta`` far above the reciprocal of the smallest observed gap means
      excitation that has vanished before the next event could see it, which is
      indistinguishable from no excitation at all.
    * ``mu`` cannot exceed the total observed arrival rate; the baseline is a
      component of that rate, not a free parameter above it.
    * ``alpha / beta`` above ~20 is explosive by any standard. The bound is kept
      well clear of 1 so that a supercritical fit is still reportable rather
      than clipped into looking stationary.
    """
    n = len(times)
    rate = n / T
    gaps = np.diff(times)
    positive = gaps[gaps > 0]
    smallest = float(positive.min()) if len(positive) else T / max(n, 1)
    typical = float(np.median(positive)) if len(positive) else T / max(n, 1)

    beta_lo = 1e-3 / T
    beta_hi = 1e3 / max(smallest, T * 1e-12)
    mu_lo = 1e-8 * rate
    mu_hi = 10.0 * rate
    alpha_lo = 1e-8 * beta_lo
    alpha_hi = 20.0 * beta_hi

    del typical  # kept above for readability of the reasoning; not a bound
    return [
        (math.log(mu_lo), math.log(mu_hi)),
        (math.log(alpha_lo), math.log(alpha_hi)),
        (math.log(beta_lo), math.log(beta_hi)),
    ]


def _at_bound(x: np.ndarray, bounds: list[tuple[float, float]], tol: float = 1e-6) -> bool:
    """Whether the solution sits on the edge of the feasible box.

    A boundary solution is not an interior optimum, so its Hessian-based
    standard errors are meaningless. Reported as non-convergence rather than
    quietly returned as a fit.
    """
    return any(
        abs(value - lo) < tol * max(1.0, abs(lo))
        or abs(value - hi) < tol * max(1.0, abs(hi))
        for value, (lo, hi) in zip(x, bounds)
    )


def _numeric_hessian(f, x: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Central-difference Hessian.

    Used instead of the optimiser's inverse-Hessian approximation, which
    L-BFGS-B builds from a limited memory of gradient steps and is not reliable
    enough to quote standard errors from.
    """
    n = len(x)
    hess = np.zeros((n, n))
    steps = np.maximum(np.abs(x) * eps, eps)
    for i in range(n):
        for j in range(i, n):
            xi, xj = np.zeros(n), np.zeros(n)
            xi[i], xj[j] = steps[i], steps[j]
            value = (
                f(x + xi + xj) - f(x + xi - xj) - f(x - xi + xj) + f(x - xi - xj)
            ) / (4.0 * steps[i] * steps[j])
            hess[i, j] = hess[j, i] = value
    return hess


def _standard_errors(
    times: np.ndarray, T: float, log_params: np.ndarray
) -> dict[str, float]:
    """Asymptotic standard errors on the natural parameters.

    The Hessian is taken in log space and mapped back by the delta method: for
    ``theta = exp(p)``, ``Var(theta) = theta^2 * Var(p)``. Returns an empty dict
    when the Hessian is not positive definite, which happens at a boundary or a
    flat optimum - reporting nothing is better than reporting a standard error
    derived from a negative variance.
    """
    try:
        hess = _numeric_hessian(lambda p: _neg_ll_log_params(p, times, T), log_params)
        cov = np.linalg.inv(hess)
        variances = np.diag(cov)
        if np.any(variances <= 0) or not np.all(np.isfinite(variances)):
            log.warning("non-positive variance in the Hessian; omitting std errors")
            return {}
        theta = np.exp(log_params)
        natural = np.sqrt(variances) * theta
        return {
            "mu": float(natural[0]),
            "alpha": float(natural[1]),
            "beta": float(natural[2]),
        }
    except np.linalg.LinAlgError:
        log.warning("singular Hessian; omitting std errors")
        return {}


def _initial_guesses(times: np.ndarray, T: float) -> list[np.ndarray]:
    """Starting points spread over decades of timescale.

    ``beta`` is the parameter that traps a single-start optimiser. Its scale is
    set by how fast excitation decays, which is unknown a priori and can differ
    by orders of magnitude between a fast order-flow cascade and a slow news
    cycle. Anchoring on the median inter-arrival time and then sweeping decades
    around it covers both without assuming either.
    """
    rate = len(times) / T
    gaps = np.diff(times)
    typical = float(np.median(gaps)) if len(gaps) else 1.0 / max(rate, 1e-9)
    typical = max(typical, 1e-9)

    guesses = []
    for beta_scale in (0.1, 1.0, 10.0, 100.0):
        beta = beta_scale / typical
        for n0 in (0.3, 0.6, 0.9):
            mu = max(rate * (1.0 - n0), 1e-9)
            alpha = n0 * beta
            guesses.append(np.log([mu, alpha, beta]))
    return guesses


def fit(
    times: Sequence[float] | np.ndarray,
    T: Optional[float] = None,
    time_unit: Literal["second", "minute", "hour", "day"] = "second",
    compute_std_errors: bool = True,
) -> HawkesParameters:
    """Fit an exponential-kernel Hawkes process by maximum likelihood.

    Args:
        times: Event times in ``time_unit``, measured from the start of the
            observation window and sorted ascending.
        T: Length of the observation window. Defaults to the last event time,
            which is a *downward-biased* choice: conditioning the window on the
            data slightly overstates the rate. Pass the real window whenever it
            is known.
        time_unit: Unit the results are expressed in. Recorded on the result so
            a branching ratio is never compared against one fitted in different
            units.
        compute_std_errors: Numerical Hessian, roughly 36 extra likelihood
            evaluations. Turn it off in a tight simulation loop.

    Returns:
        The fitted :class:`HawkesParameters`, whose ``branching_ratio``,
        ``is_stationary`` and ``excitation_half_life`` are derived rather than
        stored.

    Raises:
        ValueError: If fewer than two events are supplied, or the times are not
            sorted and within the window.
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
            "no observation window given; using the last event time (%.3f). This "
            "biases the rate upward slightly - pass the true window if known.",
            T,
        )
    if T < times[-1]:
        raise ValueError(f"window T={T} ends before the last event {times[-1]}")

    bounds = _bounds(times, T)
    best = None
    for start in _initial_guesses(times, T):
        # Clip the start into the box; a start outside it is silently projected
        # by the solver anyway, and clipping makes that explicit.
        start = np.clip(start, [lo for lo, _ in bounds], [hi for _, hi in bounds])
        result = minimize(
            _neg_ll_log_params,
            start,
            args=(times, T),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 2000, "ftol": 1e-12},
        )
        if not np.isfinite(result.fun) or result.fun >= _PENALTY:
            continue
        if best is None or result.fun < best.fun:
            best = result

    if best is None:
        raise RuntimeError("every starting point diverged; check the input times")

    mu, alpha, beta = np.exp(best.x)
    on_bound = _at_bound(best.x, bounds)
    if on_bound:
        log.warning(
            "fit rests on a parameter bound (mu=%.4g alpha=%.4g beta=%.4g). "
            "Reported as not converged: a boundary solution has no interior "
            "optimum and its standard errors would be meaningless.",
            mu,
            alpha,
            beta,
        )

    std_errors = (
        _standard_errors(times, T, best.x)
        if compute_std_errors and not on_bound
        else {}
    )

    params = HawkesParameters(
        mu=float(mu),
        alpha=float(alpha),
        beta=float(beta),
        time_unit=time_unit,
        log_likelihood=float(-best.fun),
        n_events=len(times),
        observation_window=float(T),
        converged=bool(best.success) and not on_bound,
        std_errors=std_errors,
    )

    if not params.is_stationary:
        log.warning(
            "branching ratio %.4f >= 1. This is a diagnostic, not a crash: it "
            "usually means the exponential kernel is misspecified for this data "
            "or the window spans a regime change.",
            params.branching_ratio,
        )
    return params
