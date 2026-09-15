import time
from typing import Optional, Callable

class CircuitBreakerOpenException(Exception):
    """Raised when the circuit breaker is OPEN and blocks requests."""
    pass

class CircuitBreaker:
    """
    Circuit Breaker pattern implementation to handle rate limits (HTTP 429)
    and server errors (HTTP 403/5xx) gracefully.
    """
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
        on_state_change: Optional[Callable[[str, str], None]] = None
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_state_change = time.time()
        self.on_state_change = on_state_change

    def _set_state(self, new_state: str):
        if self.state != new_state:
            old_state = self.state
            self.state = new_state
            self.last_state_change = time.time()
            if self.on_state_change:
                self.on_state_change(old_state, new_state)

    def record_success(self):
        self.failure_count = 0
        self._set_state("CLOSED")

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self._set_state("OPEN")

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        
        if self.state == "OPEN":
            if time.time() - self.last_state_change > self.recovery_timeout:
                self._set_state("HALF_OPEN")
                return True
            return False
        
        if self.state == "HALF_OPEN":
            return True
        
        return False
