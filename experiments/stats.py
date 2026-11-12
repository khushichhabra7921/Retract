"""Interval estimates for M8. The unit is the trial: probes within a trial are not independent."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

Z95 = 1.959963984540054  # two-sided 95%


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Two-sided 95% Wilson score interval for k successes in n trials."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n and n > 0, got k={k}, n={n}")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def clopper_pearson_upper_zero(n: int, alpha: float = 0.05) -> float:
    """Exact one-sided (1 - alpha) Clopper-Pearson upper bound for 0 successes in n trials."""
    # shortcut: k = 0 only (the case PLAN reports), upgrade to a beta quantile if k > 0 is needed.
    if n <= 0:
        raise ValueError("n must be positive")
    return 1 - math.pow(alpha, 1 / n)


def bootstrap_ci(
    values: Sequence[float], *, b: int = 10_000, seed: int = 0, level: float = 0.95
) -> tuple[float, float]:
    """Percentile bootstrap interval of the mean, resampling trials with replacement."""
    if not values:
        raise ValueError("no values")
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(b))
    return means[int((1 - level) / 2 * b)], means[math.ceil((1 + level) / 2 * b) - 1]


def paired_bootstrap_ci(
    a: Sequence[float], b: Sequence[float], **kw: float | int
) -> tuple[float, tuple[float, float]]:
    """(mean of a_i - b_i, its bootstrap interval) for values paired by trial."""
    diffs = [x - y for x, y in zip(a, b, strict=True)]
    return sum(diffs) / len(diffs), bootstrap_ci(diffs, **kw)  # type: ignore[arg-type]
