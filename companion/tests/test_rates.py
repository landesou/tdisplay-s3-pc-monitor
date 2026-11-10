"""差分速率：累计计数器 → MiB/s。"""

import pytest

from pcmond.rates import MIB, CounterRate


def test_first_frame_is_zero():
    """首帧没有历史基准，必须给 0.0 而不是把开机以来的累计量当速率。"""
    rate = CounterRate()
    assert rate.update(1000, now=10.0) == 0.0


def test_delta_over_elapsed_converts_to_mib_per_second():
    rate = CounterRate()
    rate.update(0, now=100.0)
    # 1.5 秒内涨了 3 MiB → 2.0 MiB/s
    assert rate.update(3 * MIB, now=101.5) == pytest.approx(2.0)


def test_counter_reset_returns_zero_without_spike():
    """计数器变小（回绕/重置）不应算出天文数字。"""
    rate = CounterRate()
    rate.update(10 * MIB, now=1.0)
    assert rate.update(1 * MIB, now=2.0) == 0.0
    assert rate.update(2 * MIB, now=3.0) == pytest.approx(1.0)


def test_zero_interval_returns_zero():
    rate = CounterRate()
    rate.update(0, now=5.0)
    assert rate.update(MIB, now=5.0) == 0.0


def test_seed_only_builds_baseline():
    rate = CounterRate()
    rate.seed(100, now=1.0)
    assert rate.last_bytes == 100
    assert rate.update(100 + MIB, now=2.0) == pytest.approx(1.0)


def test_directions_are_independent():
    down, up = CounterRate(), CounterRate()
    down.seed(0, now=0.0)
    up.seed(0, now=0.0)
    assert down.update(2 * MIB, now=1.0) == pytest.approx(2.0)
    assert up.update(0, now=1.0) == pytest.approx(0.0)
