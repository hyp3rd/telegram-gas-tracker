"""ETH Gas Price Tracker"""

import asyncio

import aiohttp
from telegram import Update
from telegram.ext import Application, CallbackContext, ConversationHandler

from core import SingletonMeta
from enums import AwaitInterval, GasTrackerState
from tracker.config import ConfigHandler
from tracker.logger import Logger

config = ConfigHandler()


class GasTracker(metaclass=SingletonMeta):
    """Gas Tracker Singleton Class"""

    def __init__(self, application: Application, logger: Logger):
        """Initialize the Tracker class."""
        self.logger = logger

        # The Telegram Application
        self.application: Application = application

        self.subscribers = set()
        self.user_thresholds = {}  # {chat_id: {"green": int, "yellow": int}}

        self.last_sent_prices = (
            {}
        )  # {chat_id: {"low": int, "average": int, "fast": int}}
        self.tracked_wallets = {}  # {wallet_address: (chat_id, last_checked_block)}

    async def fetch_gas_prices(self):
        """Fetch the current Ethereum gas prices and return them."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    config.etherscan_gastracker_url, timeout=60
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == "1":
                            result = data.get("result")
                            return {
                                "low_gas": int(result["SafeGasPrice"]),
                                "average_gas": int(result["ProposeGasPrice"]),
                                "fast_gas": int(result["FastGasPrice"]),
                                "success": True,
                            }
                        self.logger.error(
                            "Error fetching gas prices: %s", data.get("result")
                        )
                    else:
                        self.logger.error(
                            "Failed to retrieve gas data: HTTP %s", response.status
                        )
        except aiohttp.ClientError as e:
            self.logger.error(
                "Exception occurred while fetching gas prices: %s", str(e)
            )
        return {"success": False}

    # pylint: disable=too-many-arguments
    async def __send_gas_price_message(
        self,
        chat_id,
        low_gas,
        average_gas,
        fast_gas,
        thresholds,
    ):
        """Construct and send a message with the current gas prices."""
        low_emoji = (
            GasTrackerState.GREEN.value
            if low_gas <= thresholds["green"]
            else GasTrackerState.YELLOW.value
            if low_gas <= thresholds["yellow"]
            else GasTrackerState.RED.value
        )
        average_emoji = (
            GasTrackerState.GREEN.value
            if average_gas <= thresholds["green"]
            else GasTrackerState.YELLOW.value
            if average_gas <= thresholds["yellow"]
            else GasTrackerState.RED.value
        )
        fast_emoji = (
            GasTrackerState.GREEN.value
            if fast_gas <= thresholds["green"]
            else GasTrackerState.YELLOW.value
            if fast_gas <= thresholds["yellow"]
            else GasTrackerState.RED.value
        )

        # Create the message text with the appropriate emojis
        text = (
            f"▶️ *Current ETH Gas Prices*\n"
            f"Low: {low_gas} gwei {low_emoji}\n"
            f"Average: {average_gas} gwei {average_emoji}\n"
            f"Fast: {fast_gas} gwei {fast_emoji}"
        )

        # Send the message
        try:
            await self.application.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown"
            )
        except aiohttp.ClientError as e:
            self.logger.error("Failed to send message to %s: %s", chat_id, e)

    async def monitor_gas_prices(self):
        """Monitor gas prices and send an alert when they are low."""
        self.logger.info("Starting monitor_gas_prices task")

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        config.etherscan_gastracker_url, timeout=60
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("status") == "1":
                                result = data.get("result")
                                new_low_gas = int(result["SafeGasPrice"])
                                new_average_gas = int(result["ProposeGasPrice"])
                                new_fast_gas = int(result["FastGasPrice"])

                                for chat_id in self.subscribers:
                                    # Retrieve the last sent prices or use default thresholds
                                    last_prices = self.last_sent_prices.get(
                                        chat_id, {"low": 0, "average": 0, "fast": 0}
                                    )
                                    current_thresholds = self.user_thresholds.get(
                                        chat_id, {"green": 30, "yellow": 35}
                                    )

                                    # Check if the price has changed significantly
                                    if (
                                        abs(new_low_gas - last_prices["low"])
                                        > config.update_threshold
                                        or abs(new_average_gas - last_prices["average"])
                                        > config.update_threshold
                                        or abs(new_fast_gas - last_prices["fast"])
                                        > config.update_threshold
                                    ):
                                        # Update the last sent prices for this chat_id
                                        self.last_sent_prices[chat_id] = {
                                            "low": new_low_gas,
                                            "average": new_average_gas,
                                            "fast": new_fast_gas,
                                        }

                                        # Send the alert to this subscriber
                                        try:
                                            await self.__send_gas_price_message(
                                                chat_id,
                                                new_low_gas,
                                                new_average_gas,
                                                new_fast_gas,
                                                current_thresholds,
                                            )
                                        except aiohttp.ClientError as e:
                                            self.logger.error(
                                                "Failed to send alert to %s: %s",
                                                chat_id,
                                                e,
                                            )
                                    else:
                                        self.logger.debug(
                                            "No significant price change for chat %s. No alert sent.",
                                            chat_id,
                                        )

                        else:
                            self.logger.error(
                                "Failed to retrieve gas data: %s", response.status
                            )

                # Wait for 60 seconds before checking again
                await asyncio.sleep(60)

            except asyncio.CancelledError:
                # Handle the cancellation
                self.logger.warning("Gas monitor task was cancelled")
                return  # Ensure immediate exit

    async def __start_temporary_tracking(self, chat_id, duration):
        """Track gas prices and send updates every 30 seconds for a specified duration."""
        end_time = (
            asyncio.get_event_loop().time() + duration * 60
        )  # Convert minutes to seconds

        while asyncio.get_event_loop().time() < end_time:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        config.etherscan_gastracker_url, timeout=60
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("status") == "1":
                                result = data.get("result")
                                low_gas = int(result["SafeGasPrice"])
                                average_gas = int(result["ProposeGasPrice"])
                                fast_gas = int(result["FastGasPrice"])

                                # Get the user's custom thresholds or use default values
                                current_thresholds = self.user_thresholds.get(
                                    chat_id, {"green": 30, "yellow": 35}
                                )

                                # Send the current gas prices
                                try:
                                    await self.__send_gas_price_message(
                                        chat_id,
                                        low_gas,
                                        average_gas,
                                        fast_gas,
                                        current_thresholds,
                                    )
                                except aiohttp.ClientError as e:
                                    self.logger.error(
                                        "Failed to send temporary tracking alert to %s: %s",
                                        chat_id,
                                        e,
                                    )
                            else:
                                self.logger.error(
                                    "Error fetching gas prices during temporary tracking."
                                )
                        else:
                            self.logger.error(
                                "Failed to retrieve data during temporary tracking: %s",
                                response.status,
                            )

                await asyncio.sleep(30)  # Update every 30 seconds

            except asyncio.CancelledError:
                # Handle the cancellation
                self.logger.warning(
                    "Temporary tracking for chat %s was cancelled", chat_id
                )
                return  # Ensure immediate exit

    async def track(self, update, context):
        """Start temporary tracking for a specified duration."""
        self.logger.info("Received track command")
        chat_id = update.message.chat_id
        try:
            # Extract the duration from the message
            args = update.message.text.split()[1:]  # e.g., /track 5
            duration = int(args[0])  # Duration in minutes

            if 0 < duration <= 10:  # Ensure duration is between 1 and 10 minutes
                await update.message.reply_text(
                    f"Starting temporary tracking for {duration} minutes."
                )
                await self.__start_temporary_tracking(chat_id, duration)
            else:
                await update.message.reply_text(
                    "Invalid duration. Please specify a number between 1 and 10."
                )

        except (ValueError, IndexError):
            await update.message.reply_text(
                "Invalid format. Use the command like this: /track <minutes>"
            )

    async def gas(self, update, context):
        """Get and send the current Ethereum gas prices asynchronously."""
        gas_prices = await self.fetch_gas_prices()
        if gas_prices["success"]:
            chat_id = update.message.chat_id
            thresholds = self.user_thresholds.get(chat_id, {"green": 30, "yellow": 35})
            await self.__send_gas_price_message(
                chat_id,
                gas_prices["low_gas"],
                gas_prices["average_gas"],
                gas_prices["fast_gas"],
                thresholds,
            )
        else:
            await update.message.reply_text("Failed to retrieve current gas prices.")

    async def subscribe(self, update, context):
        """Subscribe the user to gas price alerts."""
        chat_id = update.message.chat_id
        if chat_id not in self.subscribers:
            self.subscribers.add(chat_id)
            await update.message.reply_text("You have subscribed to gas price alerts!")
        else:
            await update.message.reply_text("You are already subscribed.")

    async def unsubscribe(self, update, context):
        """Unsubscribe the user from gas price alerts."""
        chat_id = update.message.chat_id
        if chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            await update.message.reply_text(
                "You have unsubscribed from gas price alerts."
            )
        else:
            await update.message.reply_text("You aren't subscribed.")

    async def thresholds(self, update, context):
        """Get the current alert thresholds."""
        chat_id = update.message.chat_id
        current_thresholds = self.user_thresholds.get(
            chat_id, {"green": 30, "yellow": 35}
        )
        text = f"Current thresholds:\n{GasTrackerState.GREEN.value} Low: {current_thresholds['green']} gwei\n{GasTrackerState.YELLOW.value} Medium: {current_thresholds['yellow']} gwei"  # pylint: disable=line-too-long
        await update.message.reply_text(text)

    async def ask_for_tracking_duration(self, update: Update, context: CallbackContext):
        """Ask the user for the duration."""
        await update.message.reply_text(
            "*Please enter the duration in minutes (max 10):*", parse_mode="Markdown"
        )
        return AwaitInterval.TRACKING

    async def received_tracking_duration(
        self, update: Update, context: CallbackContext
    ):
        """Handle the received duration and start tracking."""
        try:
            chat_id = update.message.chat_id
            duration_text = update.message.text
            duration = int(duration_text)

            # Validate the duration...
            if 0 < duration <= 10:
                await update.message.reply_text(
                    f"▶️ Tracking for *{duration}* minutes. You will receive alerts *every 30 seconds.*",
                    parse_mode="Markdown",
                )
                # delay between answers
                await asyncio.sleep(1.5)
                # Start temporary tracking
                await self.__start_temporary_tracking(chat_id, duration)
            else:
                await update.message.reply_text(
                    "Invalid duration. Please specify a number between 1 and 10."
                )

        except ValueError:
            await update.message.reply_text(
                "Invalid format. Please enter a number for the duration."
            )

        return ConversationHandler.END

    async def ask_for_thresholds(self, update: Update, context: CallbackContext):
        """Ask the user for the alert thresholds."""
        await update.message.reply_text(
            "Enter the thresholds to consider the price *high* and *low* separated by a single space\ne.g. `25 30`:",
            parse_mode="Markdown",
        )
        return AwaitInterval.THRESHOLDS

    async def received_thresholds(self, update: Update, context: CallbackContext):
        """Set the alert thresholds."""
        try:
            chat_id = update.message.chat_id
            thresholds = update.message.text.split()
            green_threshold, yellow_threshold = map(int, thresholds)

            # Validate and use the thresholds
            if 0 < green_threshold < yellow_threshold:
                # Update the user's thresholds
                self.user_thresholds[chat_id] = {
                    "green": green_threshold,
                    "yellow": yellow_threshold,
                }

                await update.message.reply_text(
                    f"Thresholds updated:\n{GasTrackerState.GREEN.value} Low: {green_threshold} gwei\n{GasTrackerState.YELLOW.value} Medium: {yellow_threshold} gwei"  # pylint: disable=line-too-long
                )
            else:
                await update.message.reply_text(
                    "Please enter valid numbers with green threshold less than yellow threshold."
                )

        except (ValueError, IndexError, AttributeError):
            await update.message.reply_text(
                "Invalid format. Use the command like this:\n"
                "/set_thresholds <green_threshold> <yellow_threshold>\n"
                "For example: /set_thresholds 20 40"
            )
        return ConversationHandler.END
