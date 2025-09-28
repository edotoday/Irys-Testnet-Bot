from enum import Enum


class APIErrorType(Enum):
    INVALID_SIGNATURE = "Invalid signature."
    INVALID_CAPTCHA = "Captcha verification failed, refresh the page and try again."


class APIError(Exception):
    def __init__(self, error: str, response_data: dict | str = None):
        self.error = error
        self.response_data = response_data
        self.error_type = self._get_error_type()
        super().__init__(error)

    def _get_error_type(self) -> APIErrorType | None:
        return next(
            (error_type for error_type in APIErrorType if error_type.value == self.error_message),
            None
        )

    @property
    def error_message(self) -> str:
        if isinstance(self.response_data, dict):
            if "message" in self.response_data or "msg" in self.response_data:
                return self.response_data.get("message") or self.response_data.get("msg") or self.error

            return self.error

        return self.error

    def __str__(self):
        return self.error


class CaptchaSolvingFailed(Exception):
    """Raised when the captcha solving failed"""

    pass


class ServerError(Exception):
    """Raised when the server returns an error"""

    pass


class NoAvailableProxies(Exception):
    """Raised when there are no available proxies"""

    pass


class ProxyForbidden(Exception):
    """Raised when the proxy is forbidden"""

    pass


class EmailValidationFailed(Exception):
    """Raised when the email validation failed"""

    pass


class ComputingImageFailed(Exception):
    """Raised when the computing image failed"""

    pass


class DiscordConnectError(Exception):
    """Raised when there is an error with Discord connection"""


class RateLimitExceeded(Exception):
    """Raised when the rate limit is exceeded"""

    def __init__(self, reset_time: int):
        self.reset_time = reset_time
        super().__init__(f"Rate limit exceeded. Try again in {reset_time} seconds.")

    def __str__(self):
        return f"Rate limit exceeded. Try again in {self.reset_time} seconds."
