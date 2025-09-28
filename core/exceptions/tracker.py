from collections import defaultdict
import time
from dataclasses import dataclass
from typing import DefaultDict, List


class TooManyErrorsException(Exception):
    """Raised when too many similar errors occur in a short time period"""
    pass


@dataclass
class ErrorOccurrence:
    error_type: str
    timestamp: float


class ErrorTracker:
    def __init__(self, max_errors: int = 3, time_window: int = 60):
        """
        Initialize error tracker

        Args:
            max_errors: Maximum number of similar errors allowed in time window
            time_window: Time window in seconds to track errors
        """
        self.max_errors = max_errors
        self.time_window = time_window
        self.errors: DefaultDict[str, List[ErrorOccurrence]] = defaultdict(list)

    def add_error(self, error: Exception) -> None:
        """
        Add an error occurrence and check if threshold is exceeded

        Args:
            error: The exception that occurred

        Raises:
            TooManyErrorsException: If too many similar errors occur in time window
        """
        now = time.time()
        error_type = type(error).__name__

        self.errors[error_type].append(ErrorOccurrence(error_type, now))
        self._clean_old_errors(now)

        if len(self.errors[error_type]) >= self.max_errors:
            if error:
                raise TooManyErrorsException(
                    f"Too many {error_type} errors: {len(self.errors[error_type])} in last {self.time_window} seconds. "
                    f"Last error: {str(error)}"
                )
            else:
                raise TooManyErrorsException(
                    f"Too many {error_type} errors: {len(self.errors[error_type])} in last {self.time_window} seconds."
                )

    def _clean_old_errors(self, current_time: float) -> None:
        cutoff_time = current_time - self.time_window

        for error_type in list(self.errors.keys()):
            self.errors[error_type] = [
                err for err in self.errors[error_type]
                if err.timestamp > cutoff_time
            ]

            if not self.errors[error_type]:
                del self.errors[error_type]
