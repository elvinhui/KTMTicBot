import random

class AdaptivePoller:
    """
    Poller that calculates dynamic intervals with normal distribution jitter
    to mimic human behavior and avoid anti-bot detection, plus exponential backoff
    for rate limiting (429/403).
    """
    def __init__(
        self,
        base_interval: float = 4.5,
        jitter: float = 1.5,
        min_interval: float = 1.0,
        max_backoff: float = 120.0
    ):
        self.base_interval = base_interval
        self.jitter = jitter
        self.min_interval = min_interval
        self.max_backoff = max_backoff

    def get_next_delay(self) -> float:
        """
        Generate a random delay using a Gaussian normal distribution.
        """
        delay = random.gauss(self.base_interval, self.jitter)
        return max(self.min_interval, delay)

    def get_backoff_delay(self, consecutive_errors: int) -> float:
        """
        Calculate exponential backoff delay based on error count, with jitter.
        """
        multiplier = 2 ** min(consecutive_errors, 6)
        backoff = self.base_interval * multiplier
        jittered = backoff + random.uniform(0.5, 2.0)
        return min(self.max_backoff, jittered)
