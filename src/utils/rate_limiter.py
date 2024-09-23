import time
import threading
from collections import deque
import logging


class RateLimiter:
    """
    A thread-safe rate limiter that restricts the number of calls within a time period.
    """

    def __init__(self, max_calls: int, period: float):
        """
        Initialize the RateLimiter.

        :param max_calls: Maximum number of allowed calls within the period.
        :param period: Time window in seconds.
        """
        self.max_calls = max_calls
        self.period = period
        self.lock = threading.Lock()
        self.calls = deque()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.call_interval = period / max_calls
        self.last_call_time = time.time()

    def acquire(self):
        """
        Acquire permission to proceed with a call. If the rate limit is exceeded,
        this method will block until a slot becomes available.
        """
        while True:
            with self.lock:
                current_time = time.time()
                time_since_last_call = current_time - self.last_call_time

                if time_since_last_call >= self.call_interval:
                    # Allow the call
                    self.last_call_time = current_time
                    self.calls.append(current_time)

                    # Remove old timestamps
                    while self.calls and self.calls[0] <= current_time - self.period:
                        self.calls.popleft()

                    self.logger.debug(
                        f"Call allowed at {current_time}. Total calls: {len(self.calls)}"
                    )
                    return
                else:
                    # Calculate the time to wait until the earliest call exits the window
                    wait_time = self.call_interval - time_since_last_call
                    self.logger.warning(
                        f"Rate limit reached. Sleeping for {wait_time:.2f} seconds."
                    )

            # Sleep outside the lock to allow other threads to proceed
            time.sleep(wait_time)
