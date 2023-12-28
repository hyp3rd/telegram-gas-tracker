"""Config module."""

try:
    from .config import ConfigHandler
except ImportError as ex:
    raise ex
