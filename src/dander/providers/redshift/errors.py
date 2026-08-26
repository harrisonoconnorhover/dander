"""Sanitized Redshift provider failures shared across lazy runtime imports."""

from dander.providers import ProviderFactoryError


class RedshiftConnectionUnavailableError(ProviderFactoryError):
    """Report a bounded Redshift connection timeout as transient unavailability."""
