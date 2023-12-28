"""logger module."""

try:
    from .logger import Logger, log_formatter
except ImportError as ex:
    raise ex
