"""Configuration file for the application."""
import os

from dotenv import load_dotenv

from core import SingletonMeta


class ConfigHandler(metaclass=SingletonMeta):
    """Configuration handler."""

    def __init__(self):
        """Initialize the configuration handler."""
        load_dotenv()
        self._telegram_token = None
        self._telegram_api_url = None
        self._etherscan_api_key = None
        self._etherscan_api_url = None
        self._load_config()

    def _load_config(self):
        """Load configuration from environment variables."""
        self._telegram_token = os.getenv("TELEGRAM_TOKEN")
        self._telegram_api_url = (
            f"https://api.telegram.org/bot{self._telegram_token}/getMe"
        )
        self._etherscan_api_key = os.getenv("ETHERSCAN_API_KEY")
        self._etherscan_api_url = f"https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey={self._etherscan_api_key}"

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

    def __repr__(self):
        """Return a string representation of the object."""
        return f"ConfigHandler(telegram_token={self.telegram_token})"
