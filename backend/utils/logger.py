"""
Structured logging configuration using structlog
"""
import structlog
import logging
import sys

from backend.config import settings


def redact_sensitive_processor(logger, method_name, event_dict):
    """
    Scrub passwords, tokens, Bearer headers, 12-digit Aadhaar, and phone numbers from all log events.
    """
    try:
        from backend.services.security import redact_sensitive_dict
        return redact_sensitive_dict(event_dict)
    except Exception:
        return event_dict


def configure_logging():
    """
    Configure structlog for structured logging
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG
    )
    
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            redact_sensitive_processor,
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# Get logger
logger = structlog.get_logger()


def get_logger(name: str = None):
    """
    Get a structured logger instance
    
    Args:
        name: Optional logger name (module name)
    
    Returns:
        Configured structlog logger
    """
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()
