"""M8 interval estimates against known values."""

import pytest

from experiments.stats import bootstrap_ci, clopper_pearson_upper_zero, paired_bootstrap_ci, wilson


def test_wilson_known_values():
    lo, hi = wilson(0, 30)
    assert lo == 0.0 and hi == pytest.approx(0.1135, abs=1e-4)  # PLAN: 0/30 ~ 11.4%
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    lo, hi = wilson(30, 30)
    assert hi == pytest.approx(1.0) and lo == pytest.approx(1 - 0.1135, abs=1e-4)
    with pytest.raises(ValueError):
        wilson(3, 2)


def test_clopper_pearson_zero():
    assert clopper_pearson_upper_zero(30) == pytest.approx(0.0950, abs=1e-4)  # PLAN: ~9.5%


def test_bootstrap():
    assert bootstrap_ci([0.4] * 30) == pytest.approx((0.4, 0.4))
    lo, hi = bootstrap_ci([0.0] * 15 + [1.0] * 15)
    assert lo < 0.5 < hi and 0.25 < lo and hi < 0.75
    assert bootstrap_ci([1.0, 2.0, 3.0], seed=7) == bootstrap_ci([1.0, 2.0, 3.0], seed=7)
    mean, ci = paired_bootstrap_ci([1.0, 2.0], [0.5, 1.5])
    assert mean == pytest.approx(0.5) and ci == pytest.approx((0.5, 0.5))
