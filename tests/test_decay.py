import pytest

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.decay import decay_fraction


def test_zero_days_past_maturity_is_zero_decay() -> None:
    assert decay_fraction(0) == 0.0


def test_decay_reaches_one_at_horizon() -> None:
    assert decay_fraction(crop_params.DECAY_HORIZON_DAYS_ESTIMATED) == 1.0


def test_decay_caps_at_one_beyond_horizon() -> None:
    assert decay_fraction(crop_params.DECAY_HORIZON_DAYS_ESTIMATED * 3) == 1.0


def test_decay_is_monotonic() -> None:
    values = [decay_fraction(d) for d in range(0, crop_params.DECAY_HORIZON_DAYS_ESTIMATED + 1)]
    assert values == sorted(values)


def test_negative_days_raises() -> None:
    with pytest.raises(ValueError):
        decay_fraction(-1)
