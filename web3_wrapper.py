"""ENS Domains Tools Wrapper"""

# from ens.auto import ns
from ens import ENS  # pylint: disable=import-error
from ens.exceptions import InvalidName, ResolverNotFound, UnauthorizedError
from web3 import HTTPProvider, Web3
from web3.types import ChecksumAddress

from core import SingletonMeta
from tracker.config import ConfigHandler
from tracker.logger import Logger

config = ConfigHandler()


class Web3Wrapper(metaclass=SingletonMeta):
    """ENSWrapper class"""

    def __init__(self, logger: Logger) -> None:
        self.provider = HTTPProvider(
            f"https://mainnet.infura.io/v3/{config.infura_api_key}"
        )

        self.ns = ENS(self.provider)
        self.web3 = Web3(self.provider)
        self.logger = logger

    def resolve_ens(self, ens_name) -> ChecksumAddress | None:
        """Resolve an ENS name to an address."""

        self.logger.info("Resolving ENS name: %s", ens_name)
        try:
            eth_address = self.ns.address(name=ens_name)
            self.logger.info("ENS name resolved: %s", eth_address)
            return eth_address
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error("Error resolving ENS name: %s", e)
            return None

    def get_ens_name(self, eth_address) -> str | None:
        """Get the ENS name of an address."""

        self.logger.info("Getting ENS name of address: %s", eth_address)
        try:
            ens_name = self.ns.name(address=eth_address)
            self.logger.info("ENS name of address: %s", ens_name)
            return ens_name
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error("Error getting ENS name of address: %s", e)
            return None

    def is_valid_ens_domain(self, ens_name) -> bool:
        """Check if a string is a valid ENS domain."""

        self.logger.info("Checking if %s is a valid ENS domain", ens_name)
        try:
            is_valid = self.ns.is_valid_name(ens_name)
            self.logger.debug("Is %s a valid ENS domain? %s", ens_name, is_valid)
            return is_valid
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error(
                "Error checking if %s is a valid ENS domain: %s", ens_name, e
            )
            return False

    def is_address(self, address) -> bool:
        """Check if a string is a valid address."""

        self.logger.info("Checking if %s is a valid address", address)
        try:
            is_address = self.web3.is_address(address)
            self.logger.debug("Is %s a valid address? %s", address, is_address)
            return is_address
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error("Error checking if %s is a valid address: %s", address, e)
            return False

    def get_block_number(self) -> int:
        """Get the current block number."""

        self.logger.info("Getting the current block number")
        try:
            block_number = self.web3.eth.block_number
            self.logger.debug("Current block number: %s", block_number)
            return block_number
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error("Error getting the current block number: %s", e)
            return None

    def get_block(self, block_number) -> dict:
        """Get a block by number."""

        self.logger.info("Getting block %s", block_number)
        try:
            block = self.web3.eth.get_block(block_number, full_transactions=False)
            self.logger.debug("Block %s: %s", block_number, block)
            return block
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error("Error getting block %s: %s", block_number, e)
            return None

    def get_token_symbol_and_name(self, token_address) -> tuple:
        """Get the symbol and name of a token."""

        self.logger.info("Getting token symbol and name for %s", token_address)
        try:
            contract = self.web3.eth.contract(
                address=token_address, abi=config.erc20_abi
            )

            symbol = contract.functions.symbol().call()
            name = contract.functions.name().call()
            self.logger.debug(
                "Token symbol and name for %s: %s, %s", token_address, symbol, name
            )
            return symbol, name
        except (InvalidName, ResolverNotFound, UnauthorizedError) as e:
            self.logger.error(
                "Error getting token symbol and name for %s: %s", token_address, e
            )
            return None, None
