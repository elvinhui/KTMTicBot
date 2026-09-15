import time
import pytest
from ktm_sniper.network.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException

def test_circuit_breaker_flow():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == "CLOSED"
    assert cb.can_execute() is True

    cb.record_failure()
    assert cb.state == "CLOSED"

    cb.record_failure()
    assert cb.state == "CLOSED"

    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_execute() is False

    time.sleep(0.25)
    assert cb.can_execute() is True
    assert cb.state == "HALF_OPEN"

    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0

def test_circuit_breaker_listener():
    transitions = []
    def on_change(old, new):
        transitions.append((old, new))

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, on_state_change=on_change)
    cb.record_failure()
    assert transitions == [("CLOSED", "OPEN")]
