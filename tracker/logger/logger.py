"""Create a logger for the application.""" ""
import logging
from logging import StreamHandler

import tracker.logger.formatter as log_formatter
from tracker.config import ConfigHandler

config = ConfigHandler()


class Logger(logging.Logger):
    """Singleton logger class. It allows to create only one instance of the logger."""

    _instance = None

    def __new__(cls, *args):  # pylint: disable=unused-argument
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, name: str):
        if not self.__dict__:
            super().__init__(name)

    def configure(self):
        """Apply the logger's configuration."""
        level = getattr(logging, config.log_level.upper(), None)

        if level is None:
            level = logging.INFO

        # Remove all handlers associated with the logger object.
        for logger_handler in self.handlers:
            self.removeHandler(logger_handler)

        handler = Logger.generate_handler()

        self.addHandler(handler)

        # Clear handlers from the root logger
        logging.root.handlers = []

    @staticmethod
    def generate_handler() -> StreamHandler:
        """generate the handler for any external logger"""
        level = getattr(logging, config.log_level.upper(), None)

        if level is None:
            level = logging.INFO

        formatter = log_formatter.ColourizedFormatter(
            use_colors=True,
            fmt=config.log_format,
            datefmt=config.log_date_format,
        )

        handler = logging.StreamHandler()

        handler.setLevel(level)  # Set log level for the handler
        handler.setFormatter(formatter)
        return handler

    @staticmethod
    def get_logger(name: str) -> "Logger":
        """Get a logger for the application."""
        logger = Logger(name)
        return logger

    @staticmethod
    def get_telethon_logger() -> logging.Logger:
        """Get the Telethon logger"""
        logger = logging.getLogger("telethon")
        return logger

    @staticmethod
    def init_logger(name: str) -> "Logger":
        """Initialize a logger for the application."""
        logger = Logger(name)
        logger.configure()
        return logger
