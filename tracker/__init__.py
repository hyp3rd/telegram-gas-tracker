"""tracker module."""

try:
    import tracker.config
    import tracker.core
    import tracker.logger
except ImportError as ex:
    raise ex
