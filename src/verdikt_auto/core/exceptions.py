"""Custom exceptions hierarchy for VERDIKT-AUTO."""


class VerdiktError(Exception):
    """Base exception for all application errors."""


class ConfigError(VerdiktError):
    """Configuration loading or validation error."""


class ScannerError(VerdiktError):
    """Content source scanning error."""


class RankerError(VerdiktError):
    """Topic ranking error."""


class AiRouterError(VerdiktError):
    """AI provider routing error."""


class ProviderNotAvailable(AiRouterError):
    """All AI providers exhausted for a task."""


class ProviderRateLimited(AiRouterError):
    """Current provider hit rate limit."""


class RateLimitError(AiRouterError):
    """HTTP 429 rate limit from provider."""


class QuotaExceededError(AiRouterError):
    """Provider quota exhausted."""


class TimeoutError(AiRouterError):
    """Provider request timed out."""


class APIError(AiRouterError):
    """Provider returned an API error."""

    def __init__(self, message: str, status_code: int = 0, response_body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GenerationError(VerdiktError):
    """Content generation error."""


class ParserError(VerdiktError):
    """Post parsing or validation error."""


class FormatterError(VerdiktError):
    """Content formatting error."""


class PublisherError(VerdiktError):
    """Telegram publishing error."""


class AnalyticsError(VerdiktError):
    """Analytics computation error."""


class DashboardError(VerdiktError):
    """Dashboard server error."""


class AntiBanError(VerdiktError):
    """Rate limiting or protection error."""
