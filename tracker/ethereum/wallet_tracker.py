"""Wallet Tracker"""

import asyncio
import re
import time

import aiohttp
from aiohttp import ClientOSError, ClientSession, ClientSSLError
from botocore.exceptions import ClientError
from telegram import MessageEntity, Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import Application, CallbackContext, ConversationHandler

from core import SingletonMeta
from enums import TrackerState
from tracker.config import ConfigHandler
from tracker.ethereum.wallet_tracker_storage import WalletTrackerStorage
from tracker.logger import Logger
from web3_wrapper import Web3Wrapper

config = ConfigHandler()
logger = Logger.get_logger("tracker")


class WalletTracker(metaclass=SingletonMeta):
    """Tracker class."""

    def __init__(self, application: Application):
        """Initialize the Tracker class."""

        # The Telegram Application
        self.application: Application = application

        self.web3_wrapper = Web3Wrapper(logger)

        self.storage = WalletTrackerStorage()

    async def update_last_checked_block(
        self, chat_id, wallet_address, new_last_checked_block
    ):
        """Update the last checked block for a wallet."""
        logger.debug(
            "Updating last checked block for %s to %s",
            wallet_address,
            new_last_checked_block,
        )
        try:
            # Fetch the current list of wallets for the user
            tracked_wallets = await self.storage.get_tracked_wallets(chat_id)
            # Update the last_checked_block for the specific wallet
            for wallet in tracked_wallets:
                if wallet["wallet_address"] == wallet_address:
                    wallet["last_checked_block"] = new_last_checked_block
                    break  # Stop the loop once the wallet is found and updated

            # Update the tracked_wallets attribute in the database
            await self.storage.update_wallet(chat_id, tracked_wallets)

            logger.info(
                "Updated last_checked_block for %s to %s",
                wallet_address,
                new_last_checked_block,
            )
        except ClientError as e:
            logger.error(
                "Failed to update last checked block for wallet %s: %s",
                wallet_address,
                e,
            )

    async def monitor_wallet_transactions(self):
        """Regularly check tracked wallets for new transactions."""
        logger.info("Starting monitor_wallet_transactions task")
        while True:
            try:
                # Fetch all tracked wallets from the storage
                items = await self.storage.get_all()
                logger.debug("Tracked wallets: %s", items)

                for item in items:
                    try:
                        logger.debug("Checking item: %s", item)
                        logger.debug("Item type: %s", type(item))
                        chat_id = int(item["chat_id"])
                    except (KeyError, ValueError, TypeError) as e:
                        logger.error(
                            "Invalid chat_id for item %s: %s",
                            item,
                            e,
                        )
                        continue  # Skip this user if the chat_id is missing or invalid
                    tracked_wallets = item.get("tracked_wallets", [])

                    for wallet in tracked_wallets:
                        is_paused = wallet.get("paused", False)
                        logger.debug(
                            "Checking wallet %s for paused state: %s",
                            wallet["wallet_address"],
                            is_paused,
                        )

                        if is_paused:
                            continue  # Skip this wallet if it's paused

                        wallet_address = wallet["wallet_address"]
                        try:
                            last_checked_block = int(wallet["last_checked_block"])
                        except (KeyError, ValueError):
                            logger.error(
                                "Invalid last_checked_block for wallet %s: %s",
                                wallet_address,
                                wallet["last_checked_block"],
                            )
                            error_message = (
                                f"Invalid block for wallet `{wallet_address}`\n"
                                "Please check if the address is valid."
                            )
                            await self.application.bot.send_message(
                                chat_id=chat_id,
                                text=error_message,
                                parse_mode="Markdown",
                            )
                            continue  # Skip this wallet if the last_checked_block is missing or invalid

                        logger.debug(
                            "Checking wallet %s for new transactions since block %s",
                            wallet_address,
                            last_checked_block,
                        )

                        try:
                            new_last_checked_block = (
                                await self.__check_wallet_transactions(
                                    wallet_address, last_checked_block, chat_id
                                )
                            )
                        except Exception as e:  # pylint: disable=broad-except
                            logger.exception(
                                "Failed to check wallet %s for new transactions: %s",
                                wallet_address,
                                e,
                            )

                        if new_last_checked_block:
                            logger.debug(
                                "Updating last checked block for wallet %s to %s",
                                wallet_address,
                                new_last_checked_block,
                            )
                            await self.update_last_checked_block(
                                chat_id, wallet_address, new_last_checked_block
                            )

            except asyncio.CancelledError:
                # Handle the cancellation
                logger.warning("Wallet Tracker monitor task was cancelled")
                return  # Ensure immediate exit
            except ClientError as e:
                logger.error("Failed to fetch tracked wallets: %s", e)

    async def __send_transaction_details(
        self, message_text, chat_id, wallet_address, new_last_checked_block
    ):
        """Send the transaction details to the user."""
        try:
            chat_id = int(chat_id)
            logger.debug(message_text)
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except (
            aiohttp.ClientError,
            ClientSSLError,
            NetworkError,
            ClientOSError,
            TelegramError,
            BadRequest,
        ) as ex:
            logger.error(
                "Failed to send message to %s: %s",
                chat_id,
                ex,
                exc_info=True,
            )

            await self.application.bot.send_message(
                chat_id=chat_id,
                text="*Failed to parse the transaction*",
                parse_mode="Markdown",
            )

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
            start_time = time.time()
            total_transactions = 0
            async with session.get(config.etherscan_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.debug("Response from Etherscan: %s", data)
                    transactions = data.get("result", [])

                    if transactions:
                        total_transactions = len(transactions)

                        new_last_checked_block = int(transactions[-1]["blockNumber"])
                        for tx in transactions:
                            # Translate the contract address to a ticker or name and format the message

                            # Send a message to each subscribed user
                            message_text = await self.__process_transaction(
                                tx, wallet_address, chat_id
                            )

                            # processing_time = time.time() - start_time
                            sleep_time = self.__calculate_sleep_time(
                                total_transactions,
                                time.time() - start_time,
                                max_sleep_time=15,  # Set max sleep time to 60 seconds
                            )

                            await self.__send_transaction_details(
                                message_text,
                                chat_id,
                                wallet_address,
                                new_last_checked_block,
                            )

                            logger.debug(
                                "Transaction details sent. Sleeping for %s", sleep_time
                            )

                            await asyncio.sleep(sleep_time)

                        return new_last_checked_block
        return None

    # pylint: disable=too-many-locals
    async def __process_transaction(self, tx, wallet_address, chat_id) -> str:
        """Process a transaction and return the message text."""
        # Common transaction details
        from_address = tx["from"]
        to_address = tx["to"]
        value_wei = int(tx["value"])
        value_eth = value_wei / 10**18  # Convert from wei to ETH
        gas_used = int(tx["gasUsed"])
        gas_price = int(tx["gasPrice"])
        gas_paid = gas_used * gas_price / 10**18  # Convert from wei to ETH
        block_number = int(tx["blockNumber"])
        tx_hash = tx.get("hash", "")

        # Determine the direction of the transaction
        direction = (
            "Outgoing" if from_address.lower() == wallet_address.lower() else "Incoming"
        )

        # Check for ERC-20 token transfer (methodId: 0xa9059cbb)
        if tx["input"].startswith("0xa9059cbb") and to_address:
            # This is a token transfer, attempt to identify the token
            # token_name, token_symbol = await self.__get_token_details(to_address)
            (
                token_name,
                token_symbol,
            ) = self.web3_wrapper.get_token_symbol_and_name(to_address)
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

        # Check if the wallet has an wallet_tag
        current_wallets = await self.storage.get_tracked_wallets(chat_id)
        wallet_tag_section = ""
        for wallet in current_wallets:
            if wallet["wallet_address"] == wallet_address:
                wallet_tag = wallet.get("wallet_tag", None)
                if wallet_tag:
                    wallet_tag_section = f"Tag: {wallet_tag}"

        # try to resolve to ENS name
        __ens_addr = self.web3_wrapper.get_ens_name(from_address)
        if __ens_addr:
            from_address = __ens_addr
            __ens_addr = None
        __ens_addr = self.web3_wrapper.get_ens_name(to_address)
        if __ens_addr:
            to_address = __ens_addr

        # Construct the alert message
        message_text = (
            f"*{direction} Transaction Alert*\n"
            f"*{wallet_tag_section}*\n"
            f"From `{from_address}`\n"
            f"To: `{to_address}`\n"
            f"Asset: {asset_description}\n"
            f"Value: {str(value_eth)} ETH{str(value_usd_text)}\n"
            f"Gas Paid: {str(gas_paid)} ETH{str(gas_paid_usd_text)}\n"
            f"Block: {str(block_number)}\n\n"
            f"[View on Etherscan](https://etherscan.io/tx/{tx_hash})"
        )

        return message_text

    async def list_tracked_wallets(self, update: Update, context: CallbackContext):
        """List all tracked wallets."""
        chat_id = update.message.chat_id
        message = "🔍 *Currently tracking the following wallets:*\n"
        tracked_wallets = await self.storage.get_tracked_wallets(chat_id)

        for wallet in tracked_wallets:
            wallet_address = wallet["wallet_address"]
            last_checked_block = wallet["last_checked_block"]
            wallet_tag = wallet.get("wallet_tag", "No wallet_tag provided")
            status = "Paused" if wallet.get("paused", False) else "Active"
            message += f"- `{wallet_address}`\nTag: `{wallet_tag}`\nfrom block {last_checked_block}\nstatus: {status}\n"

        if not tracked_wallets:
            message = "🔍 You are not currently tracking any wallets."
        await update.message.reply_text(message, parse_mode="Markdown")

    async def ask_for_wallet_to_resolve(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet address to resolve."""
        await update.message.reply_text(
            "Please enter the wallet address you want to resolve:"
        )
        return TrackerState.WALLET_RESOLVED.value

    async def received_wallet_to_resolve(
        self, update: Update, context: CallbackContext
    ):
        """Handle the received wallet address and resolve it."""
        wallet_address = update.message.text.strip()
        # chat_id = update.message.chat_id
        logger.debug("Received wallet address to resolve: %s", wallet_address)

        if self.__is_valid_wallet(wallet_address):
            resolved_address = self.web3_wrapper.resolve_ens(wallet_address)
            if resolved_address:
                message_text = f"✅ Resolved wallet address: `{wallet_address}` to `{resolved_address}`"
                logger.debug(message_text)
                await update.message.reply_text(message_text, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "❌ Failed to resolve the wallet. Please check the address and try again."
                )
                logger.debug(
                    "Failed to resolve the wallet. Please check the address and try again."
                )
        else:
            await update.message.reply_text(
                "❌ Invalid wallet address. Please try again."
            )
            logger.debug("Invalid wallet address. Please try again.")

        return ConversationHandler.END

    async def ask_for_wallet(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet address."""
        await update.message.reply_text(
            "Please enter the wallet address you want to track:"
        )
        return TrackerState.WALLET_ADDRESS.value

    async def ask_for_wallet_tag(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet tag."""
        # Store the wallet address from the user's input into context.user_data
        context.user_data["wallet_address"] = update.message.text.strip()
        await update.message.reply_text(
            "*Enter a tag for this wallet (optional, it will help searching):*",
            parse_mode="Markdown",
        )
        return TrackerState.WALLET_TAG.value

    async def received_wallet(self, update: Update, context: CallbackContext):
        """Handle the received wallet address and start tracking it."""
        wallet_address = context.user_data.get("wallet_address", "").strip()
        wallet_tag = update.message.text.strip()
        chat_id = update.message.chat_id

        # Validate the wallet address
        if not self.__is_valid_wallet(wallet_address):
            await update.message.reply_text(
                "❌ Invalid wallet address. Please try again."
            )
            return ConversationHandler.END

        # Validate the wallet tag
        if not self.__is_valid_tag(wallet_tag):
            await update.message.reply_text(
                "❌ Invalid tag. Tags should start with a # followed by letters, numbers, or underscores."
            )
            return ConversationHandler.END

        # Proceed if the wallet address and tag are valid
        current_block = self.web3_wrapper.get_block_number()
        logger.debug("Current block: %s", current_block)

        if current_block:
            wallet_address_resolved = await self.storage.add_wallet(
                chat_id, wallet_address, wallet_tag, current_block
            )
            if not wallet_address_resolved:
                await update.message.reply_text(
                    "❌ Failed to resolve the wallet. Please check the address and try again later."
                )
                return ConversationHandler.END

            message = f"🔍 Starting to track wallet: `{wallet_address}`"
            if wallet_tag:
                message += f" with tag {wallet_tag}"

            message += f" from block {current_block}"
            logger.debug(message)

            # Define the position and length of the hashtag entity
            tag_position = message.find(wallet_tag)
            tag_length = len(wallet_tag)

            await update.message.reply_text(
                message,
                parse_mode="Markdown",
                entities=[
                    MessageEntity(
                        type="hashtag", offset=tag_position, length=tag_length
                    )
                ],
            )
        else:
            await update.message.reply_text(
                "❌ Unable to fetch current block number. Please try again later."
            )

        return ConversationHandler.END

    async def ask_for_wallet_untrack(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet address to stop tracking."""
        await update.message.reply_text(
            "Please enter the wallet address you want to stop tracking:"
        )
        return TrackerState.WALLET_UNTRACKED.value

    async def received_wallet_untrack(self, update: Update, context: CallbackContext):
        """Handle the received wallet address and stop tracking it."""
        wallet_address = update.message.text.strip()
        chat_id = update.message.chat_id

        current_wallets = await self.storage.get_tracked_wallets(chat_id)
        if any(
            wallet["wallet_address"] == wallet_address for wallet in current_wallets
        ):
            await self.storage.remove_wallet(chat_id, wallet_address)
            await update.message.reply_text(
                f"✅ Successfully stopped tracking wallet: `{wallet_address}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"❌ The wallet `{wallet_address}` is not being tracked, or you do not have permissions.",
                parse_mode="Markdown",
            )

        return ConversationHandler.END

    async def ask_for_wallet_to_pause(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet address to stop tracking."""
        await update.message.reply_text(
            "Please enter the wallet address you want to pause tracking:"
        )
        return TrackerState.WALLET_PAUSE.value

    async def received_wallet_to_pause(self, update: Update, context: CallbackContext):
        """Handle the received wallet address and stop tracking it."""
        wallet_address = update.message.text.strip()
        chat_id = update.message.chat_id

        # set the paused=true property to the wallet_address to allow resume tracking later
        current_wallets = await self.storage.get_tracked_wallets(chat_id)
        if any(
            wallet["wallet_address"] == wallet_address for wallet in current_wallets
        ):
            await self.storage.handle_wallet_state(chat_id, wallet_address, paused=True)
            await update.message.reply_text(
                f"✅ Successfully paused tracking wallet: `{wallet_address}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"❌ The wallet `{wallet_address}` is not being tracked, or you do not have permissions.",
                parse_mode="Markdown",
            )

    async def ask_for_wallet_to_resume(self, update: Update, context: CallbackContext):
        """Ask the user for the wallet address to stop tracking."""
        await update.message.reply_text(
            "Please enter the wallet address you want to resume tracking:"
        )
        return TrackerState.WALLET_UNPAUSE.value

    async def received_wallet_to_resume(self, update: Update, context: CallbackContext):
        """Handle the received wallet address and stop tracking it."""
        wallet_address = update.message.text.strip()
        chat_id = update.message.chat_id

        # set the paused=true property to the wallet_address to allow resume tracking later
        current_wallets = await self.storage.get_tracked_wallets(chat_id)
        if any(
            wallet["wallet_address"] == wallet_address for wallet in current_wallets
        ):
            await self.storage.handle_wallet_state(
                chat_id, wallet_address, paused=False
            )
            await update.message.reply_text(
                f"✅ Successfully resumed tracking wallet: `{wallet_address}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"❌ The wallet `{wallet_address}` is not being tracked, or you do not have permissions.",
                parse_mode="Markdown",
            )

    async def __get_eth_price_usd(self):
        """Get the current price of ETH in USD from a provider."""
        providers = [
            (
                "CoinGecko",
                "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd",
            ),
            (
                "CryptoCompare",
                "https://min-api.cryptocompare.com/data/price?fsym=ETH&tsyms=USD",
            ),
        ]

        async with ClientSession() as session:
            for name, url in providers:
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            # Extract and return price depending on the provider's response structure
                            return (
                                data["ethereum"]["usd"]
                                if name == "CoinGecko"
                                else data["USD"]
                            )
                except (ClientSSLError, ClientOSError) as ex:
                    logger.error(
                        "Exception occurred while retrieving ETH price: %s", str(ex)
                    )
        return None  # Return None if all providers fail

    def __is_valid_tag(self, tag):
        """Check if the tag is valid."""
        # Define a regular expression for a valid Telegram hashtag (letters, numbers, and underscores only)
        tag_pattern = r"^#[A-Za-z0-9_]+$"
        return re.match(tag_pattern, tag) is not None

    def __is_valid_wallet(self, wallet_address: str):
        """Check if the wallet address is valid."""
        if len(wallet_address) == 42 and wallet_address.startswith("0x"):
            # Check if the wallet address is a valid Ethereum address
            return self.web3_wrapper.is_address(wallet_address)
        # validate ENS name
        if wallet_address.endswith(".eth"):
            # Validate ENS domain names
            return self.web3_wrapper.is_valid_ens_domain(wallet_address)
        return False

    @staticmethod
    def __calculate_sleep_time(total_transactions, processing_time, max_sleep_time=60):
        """Dynamically calculate sleep time based on activity and processing time."""
        # Base sleep time on a minimum threshold to prevent zero or negative values
        base_sleep_time = max(
            7, processing_time * 0.5
        )  # 50% of the processing time or 7 seconds, whichever is higher

        # Adjust sleep time based on transaction volume
        if total_transactions > 100:  # High number of transactions
            sleep_time = base_sleep_time * 2
        elif total_transactions > 50:  # Moderate number of transactions
            sleep_time = base_sleep_time * 1.5
        else:
            sleep_time = base_sleep_time

        # Ensure the sleep time does not exceed the maximum allowed
        return min(sleep_time, max_sleep_time)
