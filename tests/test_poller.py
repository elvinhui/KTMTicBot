import pytest
from ktm_sniper.poller import AdaptivePoller

def test_adaptive_poller_gaussian_distribution():
    poller = AdaptivePoller(base_interval=5.0, jitter=1.0)
    delays = [poller.get_next_delay() for _ in range(100)]

    for d in delays:
        assert d >= 1.0

    avg = sum(delays) / len(delays)
    # Average should be close to base_interval (around 4.0 - 6.0)
    assert 4.0 <= avg <= 6.0

def test_adaptive_poller_backoff():
    poller = AdaptivePoller(base_interval=4.0, jitter=1.0, max_backoff=60.0)
    delay_0 = poller.get_backoff_delay(0)
    delay_1 = poller.get_backoff_delay(1)
    delay_2 = poller.get_backoff_delay(2)
    delay_5 = poller.get_backoff_delay(5)

    assert delay_0 >= 4.0
    assert delay_1 > delay_0
    assert delay_2 > delay_1
    assert delay_5 <= 60.0
