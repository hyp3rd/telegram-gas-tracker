"""Wallet Tracker"""

import asyncio
import re

import aiohttp
import boto3
from aiohttp import ClientOSError, ClientSession, ClientSSLError, HttpVersion11
from botocore.exceptions import ClientError, WaiterError
from telegram import MessageEntity, Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import Application, CallbackContext, ConversationHandler

from aws_utils import AWSUtils
from core import SingletonMeta
from enums import Env, TrackerState
from tracker.config import ConfigHandler
from tracker.logger import Logger
from web3_wrapper import Web3Wrapper

config = ConfigHandler()


class WalletTracker(metaclass=SingletonMeta):
    """Tracker class."""

    def __init__(self, application: Application, logger: Logger):
        """Initialize the Tracker class."""

        self.logger = logger

        # The Telegram Application
        self.application: Application = application

        self.web3_wrapper = Web3Wrapper(self.logger)

        # Handle AWS credentials when running in Docker
        if config.environment == Env.DOCKER.value:
            aws_access_key_id, aws_secret_access_key = AWSUtils.load_credentials()
            session = boto3.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
            self.db = session.resource("dynamodb")
        else:
            self.db = boto3.resource("dynamodb")

        try:
            self.table = self.db.Table("WalletTracker")
        except (ClientError, WaiterError) as e:
            self.logger.error(f"Error creating table resource: {e}")

        self.tracked_wallets_cache = (
            {}
        )  # Local cache: {wallet_address: (chat_id, last_checked_block, wallet_tag)}

    def __update_cache(self):
        """Update the local cache of tracked wallets."""
        try:
            response = self.table.scan()
            if "Items" not in response:
                return
            for item in response["Items"]:
                chat_id = int(item["chat_id"])
                tracked_wallets = item.get("tracked_wallets", [])
                self.tracked_wallets_cache[chat_id] = tracked_wallets
        except ClientError as e:
            self.logger.error(f"Error updating cache: {e}")

    async def refresh_db_cache(self):
        """Periodically refresh the local cache of tracked wallets."""
        self.logger.info("Starting refresh_db_cache task")
        while True:
            try:
                self.logger.info("Refreshing tracked wallets cache.")
                self.__update_cache()
                self.logger.info("Cache refresh complete.")
            except (ClientError, asyncio.CancelledError) as e:
                self.logger.error(f"Error refreshing cache: {e}")
            await asyncio.sleep(900)  # Sleep for 15 minutes (900 seconds)

    async def add_wallet(self, chat_id, wallet_address, wallet_tag, starting_block):
        """Add a wallet to the tracked list for a user."""
        try:
            if wallet_address.endswith(".eth"):  # rudimentary check for ENS name
                self.logger.debug("About to resolve ENS name %s", wallet_address)
                resolved_address = self.web3_wrapper.resolve_ens(
                    ens_name=wallet_address
                )
                self.logger.debug(
                    "Resolved ENS name %s to %s", wallet_address, resolved_address
                )
                if resolved_address:
                    wallet_address = resolved_address
                else:
                    self.logger.error(f"Could not resolve ENS name: {wallet_address}")
                    return None

            # Retrieve the current list of tracked wallets for the user
            response = self.table.get_item(Key={"chat_id": chat_id})
            item = response.get("Item", None)

            new_wallet = {
                "wallet_address": wallet_address,
                "wallet_tag": wallet_tag,
                "last_checked_block": starting_block,
            }

            if item:
                # Append the new wallet to the list
                tracked_wallets = item.get("tracked_wallets", [])
                tracked_wallets.append(new_wallet)
                # Update the item in the database
                self.table.update_item(
                    Key={"chat_id": chat_id},
                    UpdateExpression="SET tracked_wallets = :val",
                    ExpressionAttributeValues={":val": tracked_wallets},
                )
            else:
                # Create a new item if the user wasn't already tracking wallets
                self.table.put_item(
                    Item={"chat_id": chat_id, "tracked_wallets": [new_wallet]}
                )
            # Update the cache
            self.__update_cache()
            return wallet_address
        except ClientError as e:
            self.logger.error(f"Error adding wallet: {e}")
            return None

    async def remove_wallet(self, chat_id, wallet_address):
        """Remove a wallet from the tracked list for a user."""
        try:
            response = self.table.get_item(Key={"chat_id": chat_id})
            item = response.get("Item", None)

            if item:
                tracked_wallets = item.get("tracked_wallets", [])
                tracked_wallets = [
                    w for w in tracked_wallets if w["wallet_address"] != wallet_address
                ]
                # Update the item in the database
                self.table.update_item(
                    Key={"chat_id": chat_id},
                    UpdateExpression="SET tracked_wallets = :val",
                    ExpressionAttributeValues={":val": tracked_wallets},
                )
            # Update the cache
            self.__update_cache()
        except ClientError as e:
            self.logger.error(f"Error removing wallet: {e}")

    async def update_last_checked_block(
        self, chat_id, wallet_address, new_last_checked_block
    ):
        """Update the last checked block for a wallet."""
        self.logger.debug(
            f"Updating last checked block for {wallet_address} to {new_last_checked_block}"
        )
        try:
            # Fetch the current list of wallets for the user
            response = self.table.get_item(Key={"chat_id": chat_id})
            item = response.get("Item", None)

            if item:
                # Extract the existing list of wallets
                tracked_wallets = item["tracked_wallets"]
                # Update the last_checked_block for the specific wallet
                for wallet in tracked_wallets:
                    if wallet["wallet_address"] == wallet_address:
                        wallet["last_checked_block"] = new_last_checked_block
                        break  # Stop the loop once the wallet is found and updated

                # Update the tracked_wallets attribute in the database
                self.table.update_item(
                    Key={"chat_id": chat_id},
                    UpdateExpression="SET tracked_wallets = :val",
                    ExpressionAttributeValues={":val": tracked_wallets},
                )
                # Update the local cache
                self.tracked_wallets_cache[chat_id] = tracked_wallets
                self.logger.info(
                    f"Updated last_checked_block for {wallet_address} to {new_last_checked_block}"
                )
        except ClientError as e:
            self.logger.error(
                f"Failed to update last checked block for wallet {wallet_address}: {e}"
            )

    async def monitor_wallet_transactions(self):
        """Regularly check tracked wallets for new transactions."""
        self.logger.info("Starting monitor_wallet_transactions task")
        while True:
            try:
                # Fetch all tracked wallets from DynamoDB
                response = (
                    self.table.scan()
                )  # Only fetch wallets that are being tracked
                items = response.get("Items", [])

                for item in items:
                    chat_id = int(item["chat_id"])
                    tracked_wallets = item.get("tracked_wallets", [])

                    for wallet in tracked_wallets:
                        wallet_address = wallet["wallet_address"]
                        try:
                            last_checked_block = int(wallet["last_checked_block"])
                        except (KeyError, ValueError):
                            self.logger.error(
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

                        self.logger.debug(
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
                            self.logger.exception(
                                "Failed to check wallet %s for new transactions: %s",
                                wallet_address,
                                e,
                            )

                        if new_last_checked_block:
                            self.logger.debug(
                                "Updating last checked block for wallet %s to %s",
                                wallet_address,
                                new_last_checked_block,
                            )
                            await self.update_last_checked_block(
                                chat_id, wallet_address, new_last_checked_block
                            )

                    await asyncio.sleep(15)  # Check every 15 seconds, adjust as needed.

            except asyncio.CancelledError:
                # Handle the cancellation
                self.logger.warning("Wallet Tracker monitor task was cancelled")
                return  # Ensure immediate exit
            except ClientError as e:
                self.logger.error("Failed to fetch tracked wallets: %s", e)
                await asyncio.sleep(15)

    async def __send_transaction_details(
        self, message_text, chat_id, wallet_address, new_last_checked_block
    ):
        """Send the transaction details to the user."""
        try:
            chat_id = int(chat_id)
            self.logger.debug(message_text)
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="Markdown",
            )

            if new_last_checked_block:
                try:
                    await self.update_last_checked_block(
                        chat_id,
                        wallet_address,
                        new_last_checked_block,
                    )
                except ClientError as e:
                    self.logger.error("Failed to update last checked block: %s", e)

            await asyncio.sleep(15)  # Wait 15 second between messages to avoid flooding
        except (
            aiohttp.ClientError,
            ClientSSLError,
            NetworkError,
            ClientOSError,
            TelegramError,
            BadRequest,
        ) as ex:
            self.logger.error(
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
            async with session.get(config.etherscan_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    self.logger.debug("Response from Etherscan: %s", data)
                    transactions = data.get("result", [])

                    if transactions:
                        new_last_checked_block = int(transactions[-1]["blockNumber"])
                        for tx in transactions:
                            # Translate the contract address to a ticker or name and format the message

                            # Send a message to each subscribed user
                            message_text = await self.__process_transaction(
                                tx, wallet_address, chat_id
                            )
                            await self.__send_transaction_details(
                                message_text,
                                chat_id,
                                wallet_address,
                                new_last_checked_block,
                            )

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

        # Determine the direction of the transaction
        direction = (
            "Outgoing" if from_address.lower() == wallet_address.lower() else "Incoming"
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

        # Check if the wallet has an wallet_tag
        current_wallets = self.tracked_wallets_cache.get(chat_id, [])
        wallet_tag_section = ""
        for wallet in current_wallets:
            if wallet["wallet_address"] == wallet_address:
                wallet_tag = wallet.get("wallet_tag", None)
                if wallet_tag:
                    wallet_tag_section = f"Tag: {wallet_tag}"

        # Construct the alert message
        message_text = (
            f"*{direction} Transaction Alert*\n"
            f"*{wallet_tag_section}*\n"
            f"{'From' if direction == 'Outgoing' else 'To'}: `{from_address}`\n"
            f"Asset: {asset_description}\n"
            f"Value: {str(value_eth)} ETH{str(value_usd_text)}\n"
            f"Gas Paid: {str(gas_paid)} ETH{str(gas_paid_usd_text)}\n"
            f"Block: {str(block_number)}"
        )

        return message_text

    async def __get_token_details(self, token_address):
        """Get the token name and symbol from the Etherscan API."""
        url = f"{config.etherscan_api_url}&module=token&action=tokeninfo&contractaddress={token_address}"

        async with ClientSession() as session:
            try:
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
            except (ClientError, ClientSSLError, ClientOSError) as ex:
                self.logger.error(
                    "Exception occurred while retrieving token details: %s", str(ex)
                )
        return "Unknown Token", "Unidentified"

    async def __get_eth_price_usd(self):
        """Get the current price of Ethereum in USD."""
        url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"

        async with ClientSession(version=HttpVersion11) as session:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Extract the price of Ethereum in USD
                        return data["ethereum"]["usd"]

                    self.logger.error(
                        "Failed to retrieve ETH price, status: %s", response.status
                    )

                return None  # Return None if there was an error
            except (ClientSSLError, ClientOSError) as ex:
                self.logger.error(
                    "Exception occurred while retrieving ETH price: %s", str(ex)
                )

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
        self.logger.debug("Received wallet address to resolve: %s", wallet_address)

        if self.__is_valid_wallet(wallet_address):
            resolved_address = self.web3_wrapper.resolve_ens(wallet_address)
            if resolved_address:
                message_text = f"✅ Resolved wallet address: `{wallet_address}` to `{resolved_address}`"
                self.logger.debug(message_text)
                await update.message.reply_text(message_text, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "❌ Failed to resolve the wallet. Please check the address and try again."
                )
                self.logger.debug(
                    "Failed to resolve the wallet. Please check the address and try again."
                )
        else:
            await update.message.reply_text(
                "❌ Invalid wallet address. Please try again."
            )
            self.logger.debug("Invalid wallet address. Please try again.")

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
        current_block = await self.__get_current_block_number()
        if current_block:
            wallet_address_resolved = await self.add_wallet(
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
            self.logger.debug(message)

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

    def __is_valid_tag(self, tag):
        """Check if the tag is valid."""
        # Define a regular expression for a valid Telegram hashtag (letters, numbers, and underscores only)
        tag_pattern = r"^#[A-Za-z0-9_]+$"
        return re.match(tag_pattern, tag) is not None

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

    def __is_valid_wallet(self, wallet_address: str):
        """Check if the wallet address is valid."""
        if len(wallet_address) == 42 and wallet_address.startswith("0x"):
            # Check if the wallet address is a valid Ethereum address
            return self.web3_wrapper.is_address(wallet_address)
        # validate ENS name
        if wallet_address.endswith(".eth"):
            # Add validatation for ENS domain names

            return self.web3_wrapper.is_valid_ens_domain(wallet_address)
        return False

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

        current_wallets = self.tracked_wallets_cache.get(chat_id, [])
        if any(
            wallet["wallet_address"] == wallet_address for wallet in current_wallets
        ):
            await self.remove_wallet(chat_id, wallet_address)
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

    async def list_tracked_wallets(self, update: Update, context: CallbackContext):
        """List all tracked wallets."""
        chat_id = update.message.chat_id
        message = "🔍 *Currently tracking the following wallets:*\n"
        tracked_wallets = self.tracked_wallets_cache.get(chat_id, [])

        for wallet in tracked_wallets:
            wallet_address = wallet["wallet_address"]
            last_checked_block = wallet["last_checked_block"]
            wallet_tag = wallet.get("wallet_tag", "No wallet_tag provided")
            message += f"- `{wallet_address}`\nTag: `{wallet_tag}`\nfrom block {last_checked_block}\n"

        if not tracked_wallets:
            message = "🔍 You are not currently tracking any wallets."
        await update.message.reply_text(message, parse_mode="Markdown")
