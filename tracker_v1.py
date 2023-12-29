"""Simple Bot to track Ethereum gas prices on Etherscan"""
import asyncio
import signal
from asyncio.queues import Queue
from threading import Thread

import aiohttp
import telegram.error as telegram_error
from telegram import Bot
from telegram.ext import Updater
from uvicorn import Config, Server

from api import app
from core import SingletonMeta
from tracker.config import ConfigHandler
from tracker.logger import Logger

# pylint: disable=unused-argument
# pylint: disable=line-too-long


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
        self.bot = Bot(self.config.telegram_token)
        self.updater = Updater(self.bot, self.update_queue)

        self.subscribers = set()
        self.user_thresholds = {}  # {chat_id: {"green": int, "yellow": int}}

        self.last_sent_prices = (
            {}
        )  # {chat_id: {"low": int, "average": int, "fast": int}}

    async def main(self):
        """Start the bot and the gas price monitor."""
        loop = asyncio.get_running_loop()

        # Configure Uvicorn server
        server_config = Config(app=app, host="0.0.0.0", port=8000, loop=loop)
        server = Server(server_config)

        # Handle shutdown signals
        signals = (signal.SIGTERM, signal.SIGINT)
        for s in signals:
            loop.add_signal_handler(
                s, lambda s=s: asyncio.create_task(self.shutdown(s, server, loop))
            )

        def run_server():
            """Run the Uvicorn server in a separate thread."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(server.serve())
            loop.close()

        try:
            async with self.updater:
                # Tasks to run in parallel
                tasks = [
                    asyncio.create_task(self.updater.start_polling(), name="updater"),
                    asyncio.create_task(self.monitor_gas_prices(), name="gas_monitor"),
                    asyncio.create_task(
                        self.handle_updates(self.update_queue), name="update_handler"
                    ),
                ]

                # Run the Uvicorn server in a separate thread
                server_thread = Thread(target=run_server)
                server_thread.start()

                try:
                    # Wait for all tasks to complete (they won't unless canceled)
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    # Handle the cancellation of the asyncio.gather
                    self.logger.warning("The running tasks were cancelled")
        except asyncio.CancelledError:
            self.logger.warning(
                "CancelledError caught in main() - during updater operation"
            )

        # Ensure the server thread stops when the main tasks are cancelled
        server_thread.join()

    async def shutdown(self, sig: signal, server: Server, loop):
        """Clean up tasks and shut down the bot gracefully."""
        self.logger.warning("Received exit signal %s", sig.name)

        # Stop the updater if it's running
        if self.updater.running:
            await self.updater.stop()

        # Shut down the server if it's defined
        if server:
            server.should_exit = True

        # Cancel all outstanding tasks
        tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]

        self.logger.info("Cancelling %s outstanding tasks", len(tasks))

        for task in tasks:
            # Log the task being cancelled
            self.logger.info("Cancelling task: %s", task.get_name())
            task.cancel()
            try:
                await task  # Wait for the task to be cancelled
            except asyncio.CancelledError:
                pass  # Task cancellation is expected

        self.logger.info("All tasks have been cancelled")

        # Wait for all tasks to be cancelled
        await asyncio.gather(*tasks, return_exceptions=True)

        loop.stop()
        self.logger.info("Shutdown complete")

    async def start(self, update, context):
        """Send a message when the command /start is issued."""
        await self.help_command(update, context)

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
            "🟢"
            if low_gas <= thresholds["green"]
            else "🟡"
            if low_gas <= thresholds["yellow"]
            else "🔴"
        )
        average_emoji = (
            "🟢"
            if average_gas <= thresholds["green"]
            else "🟡"
            if average_gas <= thresholds["yellow"]
            else "🔴"
        )
        fast_emoji = (
            "🟢"
            if fast_gas <= thresholds["green"]
            else "🟡"
            if fast_gas <= thresholds["yellow"]
            else "🔴"
        )

        # Create the message text with the appropriate emojis
        text = (
            f"🚀 Current Ethereum Gas Prices 🚀\n"
            f"Low: {low_gas} gwei {low_emoji}\n"
            f"Average: {average_gas} gwei {average_emoji}\n"
            f"Fast: {fast_gas} gwei {fast_emoji}"
        )

        # Send the message
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
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

    async def set_thresholds(self, update, context):
        """Set the alert thresholds."""
        chat_id = update.message.chat_id
        try:
            # Extract green and yellow thresholds from the message
            args = update.message.text.split()[1:]  # e.g., /set_thresholds 20 40
            green_threshold, yellow_threshold = map(int, args)

            # Update the user's thresholds
            self.user_thresholds[chat_id] = {
                "green": green_threshold,
                "yellow": yellow_threshold,
            }
            text = f"Thresholds updated successfully:\n🟢 Green (Low): {green_threshold} gwei\n🟡 Yellow (Medium): {yellow_threshold} gwei"
        except (ValueError, IndexError):
            text = (
                "Invalid format. Use the command like this:\n"
                "/set_thresholds <green_threshold> <yellow_threshold>\n"
                "For example: /set_thresholds 20 40"
            )
        await update.message.reply_text(text)

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
        text = f"Current alert thresholds:\n🟢 Green (Low): {current_thresholds['green']} gwei\n🟡 Yellow (Medium): {current_thresholds['yellow']} gwei"
        await update.message.reply_text(text)

    async def help_command(self, update, context):
        """Send a message when the command /help is issued or '?' is received."""
        help_text = (
            "🤖 *Gas Tracker Bot Commands:*\n"
            "/start - Start interacting with the bot\n"
            "/gas - Get the current Ethereum gas prices\n"
            "/subscribe - Subscribe to low gas price alerts\n"
            "/unsubscribe - Unsubscribe from gas price alerts\n"
            "/thresholds - Get the current alert thresholds\n"
            "/set_thresholds - Set the alert thresholds\n"
            "/track - Start temporary tracking for a specified duration (max 10 minutes)\n"
            "/help - Show this help message\n"
            "Or just send '?' anytime you need help.\n\n"
            "To receive alerts, use the /subscribe command. When the gas price is low, "
            "you'll receive a notification!"
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

    def error(self, update, context):
        """Log errors caused by updates."""
        self.logger.warning('Update "%s" caused error "%s"', update, context.error)

    async def handle_updates(self, queue: Queue):
        """Handle updates"""
        while True:
            update = await queue.get()
            if update is None:
                break
            if update.message is None or update.message.text is None:
                continue
            text = update.message.text
            if text == "/start":
                await self.start(update, None)
            elif text == "/gas":
                await self.gas(update, None)
            elif text == "/subscribe":
                await self.subscribe(update, None)
            elif text == "/unsubscribe":
                await self.unsubscribe(update, None)
            elif text == "/thresholds":
                await self.thresholds(update, None)
            elif text.startswith("/set_thresholds"):
                await self.set_thresholds(update, None)
            elif text.startswith("/track"):
                await self.track(update, None)
            elif text in ("/help", "?"):
                await self.help_command(update, None)


if __name__ == "__main__":
    tracker = Tracker()
    asyncio.run(tracker.main())
