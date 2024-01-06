"""Wallet Tracker Storage Handler."""

import asyncio

import boto3
from botocore.exceptions import ClientError, WaiterError

from aws_utils import AWSUtils
from core import SingletonMeta
from enums import Env
from tracker.config import ConfigHandler
from tracker.logger import Logger
from web3_wrapper import Web3Wrapper

config = ConfigHandler()
logger = Logger.get_logger("tracker")


class WalletTrackerStorage(metaclass=SingletonMeta):
    """Wallet Tracker Storage Handler."""

    def __init__(self):
        """Initialize."""
        self.web3_wrapper = Web3Wrapper(logger)
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
            logger.error("Error creating table resource: %s", e, exc_info=True)

        self.tracked_wallets_cache = {}  # Cache of tracked wallets for each user

    async def update_db_cache(self):
        """Periodically refresh the local cache of tracked wallets."""
        logger.info("Starting refresh_db_cache task")
        while True:
            try:
                logger.info("Refreshing tracked wallets cache.")
                response = self.table.scan()
                if "Items" not in response:
                    return
                for item in response["Items"]:
                    chat_id = int(item["chat_id"])
                    tracked_wallets = item.get("tracked_wallets", [])
                    self.tracked_wallets_cache[chat_id] = tracked_wallets
                logger.info("Cache refresh complete.")
            except (ClientError, asyncio.CancelledError) as e:
                logger.error("Error refreshing cache: %s", e, exc_info=True)
            await asyncio.sleep(900)  # Sleep for 15 minutes (900 seconds)

    async def add_wallet(self, chat_id, wallet_address, wallet_tag, starting_block):
        """Add a wallet to the tracked list for a user."""
        try:
            if wallet_address.endswith(".eth"):  # rudimentary check for ENS name
                logger.debug("About to resolve ENS name %s", wallet_address)
                resolved_address = self.web3_wrapper.resolve_ens(
                    ens_name=wallet_address
                )
                logger.debug(
                    "Resolved ENS name %s to %s", wallet_address, resolved_address
                )
                if resolved_address:
                    wallet_address = resolved_address
                else:
                    logger.error("Could not resolve ENS name: %s", wallet_address)
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
            self.tracked_wallets_cache[chat_id] = tracked_wallets
            return wallet_address
        except ClientError as e:
            logger.error("Error adding wallet: %s", e)
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
            self.tracked_wallets_cache[chat_id] = tracked_wallets
        except ClientError as e:
            logger.error("Error removing wallet: %s", e)

    async def update_wallet(self, chat_id, tracked_wallets):
        """Update the tracked wallets for a user."""
        try:
            self.table.update_item(
                Key={"chat_id": chat_id},
                UpdateExpression="SET tracked_wallets = :val",
                ExpressionAttributeValues={":val": tracked_wallets},
            )
            # Update the cache
            self.tracked_wallets_cache[chat_id] = tracked_wallets
        except ClientError as e:
            logger.error("Error updating wallet: %s", e)

    async def get_all(self):
        """Get all the items in the table."""
        try:
            response = self.table.scan()
            if "Items" not in response:
                return []
            return response.get("Items", [])
        except ClientError as e:
            logger.error("Error getting all items: %s", e)
            return []

    async def get_tracked_wallets(self, chat_id):
        """Get the tracked wallets for a user."""
        try:
            if chat_id in self.tracked_wallets_cache:
                logger.debug("Returning tracked wallets from cache")
                return self.tracked_wallets_cache[chat_id]

            logger.debug("Returning tracked wallets from database")
            response = self.table.get_item(Key={"chat_id": chat_id})
            item = response.get("Item", None)
            if item:
                tracked_wallets = item.get("tracked_wallets", [])
                self.tracked_wallets_cache[chat_id] = tracked_wallets
                return tracked_wallets
            return []
        except ClientError as e:
            logger.error("Error getting tracked wallets: %s", e)
            return []

    async def handle_wallet_state(self, chat_id, wallet_address, paused: bool = True):
        """Set a wallet in paused state from the tracked list for a user."""
        try:
            response = self.table.get_item(Key={"chat_id": chat_id})
            item = response.get("Item", None)

            if item:
                tracked_wallets = item.get("tracked_wallets", [])
                for wallet in tracked_wallets:
                    if wallet["wallet_address"] == wallet_address:
                        wallet["paused"] = paused
                        break  # Stop the loop once the wallet is found and updated

                # Update the tracked_wallets attribute in the database
                self.table.update_item(
                    Key={"chat_id": chat_id},
                    UpdateExpression="SET tracked_wallets = :val",
                    ExpressionAttributeValues={":val": tracked_wallets},
                )
                # Update the local cache
                self.tracked_wallets_cache[chat_id] = tracked_wallets
                logger.info("Set paused state for %s to True", wallet_address)
        except ClientError as e:
            logger.error(
                "Failed to set paused state for wallet %s: %s", wallet_address, e
            )
