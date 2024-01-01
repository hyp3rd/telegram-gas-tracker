"""Wallet Tracker"""

import asyncio

import aiohttp
from aiohttp import ClientSession
from telegram import Update
from telegram.ext import Application, CallbackContext, ConversationHandler

from core import SingletonMeta
from enums import AwaitInterval
from tracker.config import ConfigHandler
from tracker.logger import Logger

config = ConfigHandler()


class WalletTracker(metaclass=SingletonMeta):
    """Tracker class."""

    def __init__(self, application: Application, logger: Logger):
        """Initialize the Tracker class."""
        self.logger = logger

        # The Telegram Application
        self.application: Application = application

        self.tracked_wallets = {}  # {wallet_address: (chat_id, last_checked_block)}

    async def monitor_wallet_transactions(self):
        """Regularly check tracked wallets for new transactions."""
        self.logger.info("Starting monitor_wallet_transactions task")
        while True:
            try:
                for wallet_address, (
                    chat_id,
                    last_checked_block,
                ) in self.tracked_wallets.items():
                    self.logger.debug(
                        "Checking wallet %s for new transactions since block %s",
                        wallet_address,
                        last_checked_block,
                    )
                    new_last_checked_block = await self.__check_wallet_transactions(
                        wallet_address, last_checked_block, chat_id
                    )
                    if new_last_checked_block:
                        self.tracked_wallets[wallet_address] = (
                            chat_id,
                            new_last_checked_block,
                        )
                await asyncio.sleep(60)  # Check every minute, adjust as needed.
            except asyncio.CancelledError:
                # Handle the cancellation
                self.logger.warning("Wallet Tracker monitor task was cancelled")
                return  # Ensure immediate exit

    async def __check_wallet_transactions(
        self, wallet_address, last_checked_block=0, chat_id=None
    ):
        """Check the wallet for new transactions since the last checked block."""
        params = {
            "module": "account",
            "action": "txlist",
            "address": wallet_address,
            "startblock": last_checked_block + 1,
            "endblock": 99999999,
            "sort": "asc",
        }
        async with ClientSession() as session:
            async with session.get(config.etherscan_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    self.logger.debug("Response from Etherscan: %s", data)
                    transactions = data.get("result", [])
                    if transactions:
                        new_last_checked_block = int(transactions[-1]["blockNumber"])
                        for tx in transactions:
                            # Translate the contract address to a ticker or name and format the message
                            message_text = await self.__process_transaction(
                                tx, wallet_address
                            )

                            # Send a message to each subscribed user
                            try:
                                await self.application.bot.send_message(
                                    chat_id=chat_id,
                                    text=message_text,
                                    parse_mode="Markdown",
                                )
                            except aiohttp.ClientError as ex:
                                self.logger.error(
                                    "Failed to send message to %s: %s", chat_id, ex
                                )

                        return new_last_checked_block
        return None

    async def __process_transaction(self, tx, wallet_address):
        """Process a transaction and return the message text."""
        # Common transaction details
        from_address = tx["from"]
        to_address = tx["to"]
        value_wei = int(tx["value"])
        value_eth = value_wei / 10**18  # Convert from wei to ETH
        gas_used = int(tx["gasUsed"])
        gas_price = int(tx["gasPrice"])
        gas_paid = gas_used * gas_price / 10**18  # Convert from wei to ETH
        block_number = tx["blockNumber"]

        # Determine the direction of the transaction
        direction = (
            "Incoming" if to_address.lower() == wallet_address.lower() else "Outgoing"
        )

        # Check for ERC-20 token transfer (methodId: 0xa9059cbb)
        if tx["input"].startswith("0xa9059cbb") and to_address:
            # This is a token transfer, attempt to identify the token
            token_name, token_symbol = await self.__get_token_details(to_address)
            asset_description = f"{token_symbol} Token ({token_name})"
        else:
            # Assume it's ETH if we can't identify the token
            asset_description = "ETH"

        # Fetch the current price of ETH in USD
        eth_price_usd = await self.__get_eth_price_usd()
        if eth_price_usd:
            value_usd = value_eth * eth_price_usd  # Convert ETH value to USD
            gas_paid_usd = gas_paid * eth_price_usd  # Convert gas paid to USD
            value_usd_text = f" (${value_usd:,.2f} USD)"
            gas_paid_usd_text = f" (${gas_paid_usd:,.2f} USD)"
        else:
            # Fallback in case the ETH price couldn't be fetched
            value_usd_text = ""
            gas_paid_usd_text = ""

        # Construct the alert message
        message_text = (
            f"*{direction} Transaction Alert*\n"
            f"{'From' if direction == 'Outgoing' else 'To'}: `{from_address}`\n"
            f"Asset: {asset_description}\n"
            f"Value: {value_eth} ETH{value_usd_text}\n"
            f"Gas Paid: {gas_paid} ETH{gas_paid_usd_text}\n"
            f"Block: {block_number}"
        )

        return message_text

    async def __get_token_details(self, token_address):
        """Get the token name and symbol from the Etherscan API."""
        url = f"{config.etherscan_api_url}&module=token&action=tokeninfo&contractaddress={token_address}"

        async with ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    # Check if the response is successful and has the necessary information
                    if (
                        data["status"] == "1"
                        and "result" in data
                        and len(data["result"]) > 0
                    ):
                        token_info = data["result"][0]
                        token_name = token_info.get("tokenName", "Unknown Token")
                        token_symbol = token_info.get("tokenSymbol", "Unidentified")
                        return token_name, token_symbol

                    self.logger.error(
                        "Failed to retrieve token details or no details available for address: %s",
                        token_address,
                    )
                else:
                    self.logger.error(
                        "Etherscan API response error, status: %s", response.status
                    )

        return "Unknown Token", "Unidentified"

    async def __get_eth_price_usd(self):
        """Get the current price of Ethereum in USD."""
        url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"

        async with ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    # Extract the price of Ethereum in USD
                    return data["ethereum"]["usd"]
                else:
                    self.logger.error(
                        "Failed to retrieve ETH price, status: %s", response.status
                    )

        return None  # Return None if there was an error

    async def ask_for_wallet(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet address."""
        await update.message.reply_text(
            "Please enter the wallet address you want to track:"
        )
        return AwaitInterval.WALLET_ADDRESS

    async def received_wallet(self, update: Update, context: CallbackContext):
        """Handle the received wallet address and start tracking it."""
        wallet_address = update.message.text.strip()
        chat_id = update.message.chat_id

        if self.__is_valid_wallet(wallet_address):
            current_block = await self.__get_current_block_number()
            if current_block:
                # Start tracking the wallet
                self.tracked_wallets[wallet_address] = (chat_id, current_block)
                self.logger.info(
                    "Tracking wallet %s for chat %s starting at block %s.",
                    wallet_address,
                    chat_id,
                    current_block,
                )
                await update.message.reply_text(
                    f"🔍 Starting to track wallet: `{wallet_address}` from block {current_block}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "❌ Unable to fetch current block number. Please try again later."
                )
        else:
            await update.message.reply_text(
                "❌ Invalid wallet address. Please try again."
            )

        return ConversationHandler.END

    async def __get_current_block_number(self):
        """Get the current block number from the Ethereum blockchain."""
        params = {
            "module": "proxy",
            "action": "eth_blockNumber",
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    config.etherscan_api_url, params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # The block number is returned as a hex string, so convert it to an integer
                        block_number = int(data["result"], 16)
                        return block_number

                    self.logger.error(
                        "Failed to fetch current block number: HTTP %s",
                        response.status,
                    )
            except aiohttp.ClientError as ex:
                self.logger.error(
                    "Exception occurred while fetching current block number: %s",
                    str(ex),
                )

        return None  # Return None if the fetch was unsuccessful

    def __is_valid_wallet(self, wallet_address):
        # Implement your validation logic, for example, checking address length and prefix
        return len(wallet_address) == 42 and wallet_address.startswith("0x")
