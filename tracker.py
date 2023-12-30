"""Simple Bot to track Ethereum gas prices on Etherscan"""
import asyncio
from asyncio.queues import Queue

import aiohttp
from telegram import MenuButtonCommands, BotCommand, Update
import telegram.error as telegram_error
from telegram.ext import (
    Application,
    CommandHandler,
    InvalidCallbackData,
    Updater,
    ExtBot,
    ConversationHandler,
    MessageHandler,
    CallbackContext,
    filters,
    # ContextTypes,
)
from uvicorn import Config, Server

from api import app
from core import SingletonMeta
from tracker.config import ConfigHandler
from tracker.logger import Logger

# pylint: disable=unused-argument
# pylint: disable=line-too-long

# Define states
AWAITING_DURATION = 1
AWAITING_THRESHOLDS = 2
GREEN_EMOJI = "🟢"
YELLOW_EMOJI = "🟡"
RED_EMOJI = "🔴"


class Tracker(metaclass=SingletonMeta):  # pylint: disable=too-many-instance-attributes
    """Tracker class."""

    def __init__(self):
        """Initialize the Tracker class."""
        self.config = ConfigHandler()
        self.logger = Logger.init_logger("tracker")

        # Enable logging
        self.logger.configure()

        # Create an asyncio Queue
        self.update_queue: Queue = asyncio.Queue()
        # Initialize the Bot and Updater
        self.application = (
            Application.builder()
            .updater(
                Updater(
                    bot=ExtBot(self.config.telegram_token),
                    update_queue=self.update_queue,
                )
            )
            .build()
        )

        self.subscribers = set()
        self.user_thresholds = {}  # {chat_id: {"green": int, "yellow": int}}

        self.last_sent_prices = (
            {}
        )  # {chat_id: {"low": int, "average": int, "fast": int}}

    async def start(self, update, context):
        """Send a message when the command /start is issued."""
        await self.help_command(update, context)

    async def set_menu_button(self, chat_id=None):
        """Set the menu button."""
        try:
            help_command = BotCommand(command="help", description="Get Help")
            gas_command = BotCommand(
                command="gas", description="Fetch the current GAS fees"
            )
            subscribe_command = BotCommand(
                command="subscribe", description="Subscribe to GAS Alerts"
            )
            unsubscribe_command = BotCommand(
                command="unsubscribe", description="Unsubscribe to GAS Alerts"
            )
            thresholds_command = BotCommand(
                command="thresholds", description="Show the current thresholds"
            )
            set_thresholds_command = BotCommand(
                command="set_thresholds", description="Set the custom thresholds"
            )
            track_command = BotCommand(
                command="track", description="Track the current GAS fees"
            )

            commands_set = await self.application.bot.set_my_commands(
                [
                    help_command,
                    gas_command,
                    subscribe_command,
                    unsubscribe_command,
                    thresholds_command,
                    set_thresholds_command,
                    track_command,
                ]
            )
            self.logger.info("Commands set successfully: %s", commands_set)
            await self.application.bot.set_chat_menu_button(
                chat_id=chat_id, menu_button=MenuButtonCommands()
            )
            self.logger.info("Menu button set successfully")
        except (
            telegram_error.BadRequest,
            telegram_error.TelegramError,
            TypeError,
        ) as e:
            self.logger.error("Failed to set menu button: %s", e, exc_info=True)

    async def fetch_gas_prices(self):
        """Fetch the current Ethereum gas prices and return them."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.config.etherscan_gastracker_url, timeout=60
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
    async def send_gas_price_message(
        self,
        chat_id,
        low_gas,
        average_gas,
        fast_gas,
        thresholds,
    ):
        """Construct and send a message with the current gas prices."""
        low_emoji = (
            GREEN_EMOJI
            if low_gas <= thresholds["green"]
            else YELLOW_EMOJI
            if low_gas <= thresholds["yellow"]
            else RED_EMOJI
        )
        average_emoji = (
            GREEN_EMOJI
            if average_gas <= thresholds["green"]
            else YELLOW_EMOJI
            if average_gas <= thresholds["yellow"]
            else RED_EMOJI
        )
        fast_emoji = (
            GREEN_EMOJI
            if fast_gas <= thresholds["green"]
            else YELLOW_EMOJI
            if fast_gas <= thresholds["yellow"]
            else RED_EMOJI
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
        self.logger.info("Starting monitor_gas_prices task")  # Unique start log

        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self.config.etherscan_gastracker_url, timeout=60
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
                                        > self.config.update_threshold
                                        or abs(new_average_gas - last_prices["average"])
                                        > self.config.update_threshold
                                        or abs(new_fast_gas - last_prices["fast"])
                                        > self.config.update_threshold
                                    ):
                                        # Update the last sent prices for this chat_id
                                        self.last_sent_prices[chat_id] = {
                                            "low": new_low_gas,
                                            "average": new_average_gas,
                                            "fast": new_fast_gas,
                                        }

                                        # Send the alert to this subscriber
                                        try:
                                            await self.send_gas_price_message(
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

    async def start_temporary_tracking(self, chat_id, duration):
        """Track gas prices and send updates every 30 seconds for a specified duration."""
        end_time = (
            asyncio.get_event_loop().time() + duration * 60
        )  # Convert minutes to seconds

        while asyncio.get_event_loop().time() < end_time:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        self.config.etherscan_gastracker_url, timeout=60
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
                                    await self.send_gas_price_message(
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
                await self.start_temporary_tracking(chat_id, duration)
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
            await self.send_gas_price_message(
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
        text = f"Current alert thresholds:\n{GREEN_EMOJI} Green (Low): {current_thresholds['green']} gwei\n{YELLOW_EMOJI} Yellow (Medium): {current_thresholds['yellow']} gwei"
        await update.message.reply_text(text)

    async def ask_for_tracking_duration(self, update: Update, context: CallbackContext):
        """Ask the user for the duration."""
        await update.message.reply_text(
            "*Please enter the duration in minutes (max 10):*", parse_mode="Markdown"
        )
        return AWAITING_DURATION

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
                await self.start_temporary_tracking(chat_id, duration)
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
        return AWAITING_THRESHOLDS

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
                    f"Thresholds updated successfully:\n{GREEN_EMOJI} Green (Low): {green_threshold} gwei\n{YELLOW_EMOJI} Yellow (Medium): {yellow_threshold} gwei"
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

    async def cancel(self, update: Update, context: CallbackContext):
        """Cancel and end the conversation."""
        await update.message.reply_text("Operation cancelled.")
        return ConversationHandler.END

    async def help_command(self, update, context):
        """Send a message when the command /help is issued or '?' is received."""
        help_text = (
            "👁️‍🗨️ *ETH GAS Tracker:*\n"
            "/start - Start interacting with the bot\n"
            "/gas - Get the current ETH gas prices\n"
            "/subscribe - Subscribe to low gas price alerts\n"
            "/unsubscribe - Unsubscribe from gas price alerts\n"
            "/thresholds - Get the current alert thresholds\n"
            "/set_thresholds - Set the alert thresholds\n"
            "/track - Track the gas fees for a specified duration (max 10 min)\n"
            "/help - Show this help message\n\n"
            "To receive alerts, use the /subscribe command. When the gas price is low, "
            "you'll receive a notification. You can also set custom alert thresholds."
        )
        try:
            # Escape underscores for markdown
            help_text = help_text.replace("_", "\\_")
            await update.message.reply_text(help_text, parse_mode="Markdown")

        except (
            asyncio.TimeoutError,
            aiohttp.ClientError,
            asyncio.CancelledError,
            telegram_error.BadRequest,
        ) as ex:
            self.logger.exception(
                "Exception handling the help command: %s", ex, exc_info=True
            )

    async def main(self):
        """Start the bot and the gas price monitor."""
        loop = asyncio.new_event_loop()

        # Configure Uvicorn server
        server_config = Config(app=app, host="0.0.0.0", port=8000, loop=loop)
        server = Server(server_config)

        try:
            self.logger.info("Starting the bot")
            await self.application.initialize()

            # Define conversation handler for '/track' command
            track_conv_handler = ConversationHandler(
                entry_points=[CommandHandler("track", self.ask_for_tracking_duration)],
                states={
                    AWAITING_DURATION: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self.received_tracking_duration,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )

            thresholds_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("set_thresholds", self.ask_for_thresholds)
                ],
                states={
                    AWAITING_THRESHOLDS: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND, self.received_thresholds
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )

            # Add conversation handlers to the application
            self.application.add_handler(track_conv_handler)
            self.application.add_handler(thresholds_conv_handler)

            # Add handlers for Telegram commands
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("start", self.start))
            self.application.add_handler(CommandHandler("gas", self.gas))
            self.application.add_handler(CommandHandler("subscribe", self.subscribe))
            self.application.add_handler(
                CommandHandler("unsubscribe", self.unsubscribe)
            )
            self.application.add_handler(CommandHandler("thresholds", self.thresholds))
            self.application.add_error_handler(self.error_handler)

            self.logger.info("Handlers initialized")

            await self.set_menu_button()

            # Run application and webserver together
            async with self.application.updater:
                await self.application.start()
                asyncio.create_task(self.monitor_gas_prices())
                await self.application.updater.start_polling()
                await server.serve()
                await self.application.updater.stop()
                await self.application.stop()

        except (
            asyncio.CancelledError,
            KeyboardInterrupt,
            AttributeError,
            InvalidCallbackData,
        ):
            self.logger.exception("The application was cancelled")

        # Ensure the server thread stops when the main tasks are cancelled
        finally:
            server.should_exit = True  # Stop the Uvicorn server

    async def error_handler(self, update, context):
        """Handle errors."""
        self.logger.error('Update "%s" caused error "%s"', update, context.error)
        if update:
            try:
                await update.message.reply_text(
                    "An error occurred. Please try again later."
                )
            except Exception as e:  # pylint: disable=broad-except
                self.logger.error("Error while sending error message to user: %s", e)


if __name__ == "__main__":
    tracker = Tracker()
    asyncio.run(tracker.main())
