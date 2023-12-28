"""Configuration file for the application."""
import os

from dotenv import load_dotenv

from core import SingletonMeta


class ConfigHandler(
    metaclass=SingletonMeta
):  # pylint: disable=too-many-instance-attributes
    """Configuration handler."""

    def __init__(self):
        """Initialize the configuration handler."""
        load_dotenv()
        self._update_threshold = (
            5  # Only send an update if the price changes by more than this amount
        )
        self._telegram_token = None
        self._telegram_api_url = None
        self._etherscan_api_key = None
        self._etherscan_api_url = None
        self._etherscan_gastracker_url = None
        self._log_level = None
        self._log_format = None
        self._log_date_format = None
        self._load_config()

    def _load_config(self):
        """Load configuration from environment variables."""

        self._update_threshold = int(os.getenv("UPDATE_THRESHOLD", "5"))

        self._telegram_token = os.getenv("TELEGRAM_TOKEN")
        self._telegram_api_url = (
            f"https://api.telegram.org/bot{self._telegram_token}/getMe"
        )
        self._etherscan_api_key = os.getenv("ETHERSCAN_API_KEY")
        self._etherscan_api_url = (
            f"https://api.etherscan.io/api?apikey={self._etherscan_api_key}"
        )
        self._etherscan_gastracker_url = (
            f"{self._etherscan_api_url}&module=gastracker&action=gasoracle"
        )

        self._log_level = os.getenv("LOG_LEVEL", "INFO")
        self._log_format = os.getenv(
            "LOG_FORMAT", "%(asctime)s %(levelprefix)s %(message)s"
        )
        self._log_date_format = os.getenv("LOG_DATE_FORMAT", "%Y-%m-%d %H:%M:%S")

    @property
    def update_threshold(self):
        """Return the update threshold."""
        return self._update_threshold

    @property
    def telegram_token(self):
        """Return the Telegram bot token."""
        return self._telegram_token

    @property
    def telegram_api_url(self):
        """Return the Telegram API URL."""
        return self._telegram_api_url

    @property
    def etherscan_api_key(self):
        """Return the Etherscan API key."""
        return self._etherscan_api_key

    @property
    def etherscan_api_url(self):
        """Return the Etherscan API URL."""
        return self._etherscan_api_url

    @property
    def etherscan_gastracker_url(self):
        """Return the Etherscan Gas Tracker URL."""
        return self._etherscan_gastracker_url

    @property
    def log_level(self):
        """Return the log level."""
        return self._log_level

    @property
    def log_format(self):
        """Return the log format."""
        return self._log_format

    @property
    def log_date_format(self):
        """Return the log date format."""
        return self._log_date_format

    def __repr__(self):
        """Return a string representation of the object."""
        return f"ConfigHandler(telegram_token={self.telegram_token})"
