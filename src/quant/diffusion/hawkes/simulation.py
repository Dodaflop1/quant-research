"""Simulate a univariate Hawkes process with an exponential kernel.

Two algorithms, for two different jobs.

:func:`simulate_cluster` uses the immigrant-offspring branching construction.
Every event knows its parent, so a simulated sample carries the ground truth
that real data never can. This is the sample the estimator is validated against:
if MLE cannot recover parameters it generated itself, no result it produces on
Kalshi trades means anything. It requires ``alpha / beta < 1``, because a
supercritical process produces infinite descendants and the recursion does not
terminate.

:func:`simulate_thinning` uses Ogata's thinning algorithm. It makes no
stationarity assumption, so it is the one to reach for when deliberately
simulating near or above the critical point - which matters here, because
published order-flow branching ratios sit close enough to 1 that estimator
behaviour in that regime is a real question rather than a corner case.

Both take an explicit ``seed``. A simulation study whose numbers cannot be
reproduced is an anecdote.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


def simulate_cluster(
    mu: float,
    alpha: float,
    beta: float,
    T: float,
    seed: Optional[int] = None,
    max_events: int = 2_000_000,
) -> tuple[np.ndarray, np.ndarray]:
    """Simulate by immigrant-offspring branching.

    Args:
        mu: Baseline (immigrant) intensity, events per unit time.
        alpha: Excitation amplitude.
        beta: Kernel decay rate.
        T: Length of the observation window, starting at zero.
        seed: PRNG seed.
        max_events: Abort above this many events, rather than exhausting memory.

    Returns:
        ``(times, parents)`` sorted by time. ``parents`` holds the index into
        ``times`` of each event's trigger, or ``-1`` for an immigrant.

    Raises:
        ValueError: If the branching ratio is at or above one, where the
            construction does not terminate.
    """
    n = alpha / beta
    if n >= 1.0:
        raise ValueError(
            f"branching ratio {n:.4f} >= 1: the cluster construction produces "
            "infinitely many offspring. Use simulate_thinning for the "
            "supercritical case."
        )
    if T <= 0:
        raise ValueError("T must be positive")

    rng = np.random.default_rng(seed)

    n_immigrants = rng.poisson(mu * T)
    times = list(rng.uniform(0.0, T, size=n_immigrants))
    parents = [-1] * n_immigrants

    # Breadth-first over generations. Each event spawns Poisson(n) offspring at
    # Exp(beta) delays; offspring past T are discarded but their own subtrees
    # are not explored, which is correct because delays are strictly positive.
    frontier = list(range(len(times)))
    while frontier:
        if len(times) > max_events:
            raise ValueError(
                f"simulation exceeded {max_events} events; branching ratio "
                f"{n:.4f} with T={T} is too large for this window"
            )
        next_frontier: list[int] = []
        counts = rng.poisson(n, size=len(frontier))
        for parent_idx, count in zip(frontier, counts):
            if count == 0:
                continue
            delays = rng.exponential(1.0 / beta, size=count)
            for delay in delays:
                child = times[parent_idx] + delay
                if child >= T:
                    continue
                times.append(child)
                parents.append(parent_idx)
                next_frontier.append(len(times) - 1)
        frontier = next_frontier

    if not times:
        return np.empty(0), np.empty(0, dtype=int)

    order = np.argsort(times, kind="stable")
    sorted_times = np.asarray(times, dtype=float)[order]

    # Parent indices refer to pre-sort positions and must be remapped, or every
    # downstream check of the branching structure silently reads the wrong
    # ancestor.
    remap = np.empty(len(order), dtype=int)
    remap[order] = np.arange(len(order))
    old_parents = np.asarray(parents, dtype=int)
    sorted_parents = np.where(old_parents < 0, -1, remap[np.maximum(old_parents, 0)])[
        order
    ]
    return sorted_times, sorted_parents


def simulate_thinning(
    mu: float,
    alpha: float,
    beta: float,
    T: float,
    seed: Optional[int] = None,
    max_events: int = 2_000_000,
) -> np.ndarray:
    """Simulate by Ogata's thinning algorithm.

    Valid for any parameters, including the supercritical case, though a
    supercritical process will hit ``max_events`` rather than finish gracefully.

    The upper bound used for thinning is the intensity immediately after the
    current time. Between events the intensity only decays, so that value
    dominates the intensity everywhere until the next accepted event, which is
    what makes the acceptance step exact rather than approximate.
    """
    if T <= 0:
        raise ValueError("T must be positive")

    rng = np.random.default_rng(seed)
    times: list[float] = []
    t = 0.0
    decay_sum = 0.0  # sum of exp(-beta * (t - t_i)) over accepted events
    last = 0.0

    while True:
        intensity_now = mu + alpha * decay_sum
        if intensity_now <= 0:
            break
        t = t + rng.exponential(1.0 / intensity_now)
        if t >= T:
            break

        decayed = decay_sum * math.exp(-beta * (t - last))
        last = t
        decay_sum = decayed

        if rng.uniform(0.0, intensity_now) <= mu + alpha * decay_sum:
            times.append(t)
            decay_sum += 1.0
            if len(times) > max_events:
                raise ValueError(
                    f"simulation exceeded {max_events} events; the process is "
                    f"likely supercritical (branching ratio {alpha / beta:.4f})"
                )

    return np.asarray(times, dtype=float)
