"""Configuration file for the application."""
import os

from dotenv import load_dotenv

from aws_utils import AWSUtils
from core import SingletonMeta
from enums import Env


class ConfigHandler(
    metaclass=SingletonMeta
):  # pylint: disable=too-many-instance-attributes
    """Configuration handler."""

    def __init__(self):
        """Initialize the configuration handler."""

        self._ennvironment = None
        self._update_threshold = (
            5  # Only send an update if the price changes by more than this amount
        )
        self._telegram_token = None
        self._telegram_api_url = None
        self._etherscan_api_key = None
        self._etherscan_api_url = None
        self._etherscan_gastracker_url = None
        self._infura_api_key = None
        self._log_level = None
        self._log_format = None
        self._log_date_format = None

    def bootstrap(self, attempt=0):
        """Load environment variables."""
        self._ennvironment = (
            Env.DOCKER.value if os.getenv("DOCKER_ENV") else Env.LOCAL.value
        )

        if self._ennvironment == Env.DOCKER.value:
            load_dotenv("/root/.env")
        else:
            load_dotenv(".env")

        self._load_config()

    def _load_config(self):
        """Load configuration from environment variables."""

        self._update_threshold = int(os.getenv("UPDATE_THRESHOLD", "5"))

        self._telegram_token = os.getenv("TELEGRAM_TOKEN")
        self._etherscan_api_key = os.getenv("ETHERSCAN_API_KEY")
        self._infura_api_key = os.getenv("INFURA_API_KEY")

        if AWSUtils.is_aws_environment():
            # _telegram_token_secret = AWSUtils.get_secret_value(
            #     "prod/telegram-gas-tracker/TELEGRAM_TOKEN"
            # )
            self._telegram_token = AWSUtils.get_secret_value(self._telegram_token)

            # _etherscan_api_key_secret = AWSUtils.get_secret_value(
            #     "prod/telegram-gas-tracker/ETHERSCAN_API_KEY"
            # )
            self._etherscan_api_key = AWSUtils.get_secret_value(self._etherscan_api_key)

            self._infura_api_key = AWSUtils.get_secret_value(self._infura_api_key)

        self._telegram_api_url = f"https://api.telegram.org/bot{self._telegram_token}/"
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
    def environment(self):
        """Return the environment."""
        return self._ennvironment

    @property
    def aws_credentials_timeout(self):
        """Return the update threshold."""
        return 60

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
    def infura_api_key(self):
        """Return the Infura API key."""
        return self._infura_api_key

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
