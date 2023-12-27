"""Simple Bot to track Ethereum gas prices on Etherscan"""
import asyncio
import logging
import os
import signal
from asyncio.queues import Queue
from threading import Thread

import aiohttp
import telegram.error as telegram_error
from dotenv import load_dotenv
from telegram import Bot
from telegram.ext import Updater
from uvicorn import Config, Server

from api import app

# pylint: disable=unused-argument
# pylint: disable=line-too-long

# Load environment variables from .env file
load_dotenv()

# Bot Token from BotFather
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
# Etherscan API Key
ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY')
# Etherscan API URL for gas tracking
ETHERSCAN_API_URL = f"https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey={ETHERSCAN_API_KEY}"

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Create an asyncio Queue
update_queue: Queue = asyncio.Queue()

# Initialize the Bot and Updater
bot = Bot(TELEGRAM_TOKEN)
updater = Updater(bot, update_queue)

subscribers = set()
user_thresholds = {}  # {chat_id: {"green": int, "yellow": int}}

last_sent_prices = {}  # {chat_id: {"low": int, "average": int, "fast": int}}
UPDATE_THRESHOLD = 5  # Only send an update if the price changes by more than this amount

# Define command handlers


async def gas(update, context):
    """Get and send the current Ethereum gas prices asynchronously."""
    async with aiohttp.ClientSession() as session:
        async with session.get(ETHERSCAN_API_URL, timeout=60) as response:
            # Ensure the API call was successful
            if response.status == 200:
                data = await response.json()
                if data.get("status") == "1":
                    result = data.get("result")

                    # Define thresholds
                    green_threshold = 30
                    yellow_threshold = 35

                    # Convert gas prices to integers
                    low_gas = int(result['SafeGasPrice'])
                    average_gas = int(result['ProposeGasPrice'])
                    fast_gas = int(result['FastGasPrice'])

                    # Determine the emoji for each gas price
                    low_emoji = "🟢" if low_gas <= green_threshold else "🟡" if low_gas <= yellow_threshold else "🔴"
                    average_emoji = "🟢" if average_gas <= green_threshold else "🟡" if average_gas <= yellow_threshold else "🔴"
                    fast_emoji = "🟢" if fast_gas <= green_threshold else "🟡" if fast_gas <= yellow_threshold else "🔴"

                    # Create the message text with the appropriate emojis
                    text = (
                        f"🚀 Current Ethereum Gas Prices 🚀\n"
                        f"Low: {low_gas} gwei {low_emoji}\n"
                        f"Average: {average_gas} gwei {average_emoji}\n"
                        f"Fast: {fast_gas} gwei {fast_emoji}"
                    )
                else:
                    text = "Error fetching gas prices."
            else:
                text = f"Failed to retrieve data: {response.status}"

    # As we're in an async function, use 'await' to send the message
    await update.message.reply_text(text)


def error(update, context):
    """Log errors caused by updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)


async def help_command(update, context):
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
        help_text = help_text.replace('_', '\\_')
        await update.message.reply_text(help_text, parse_mode="Markdown")

    except (asyncio.TimeoutError, aiohttp.ClientError, asyncio.CancelledError, telegram_error.BadRequest):
        logger.exception("Exception handling the help command")


async def handle_updates(queue: Queue):
    """Handle updates"""
    while True:
        update = await queue.get()
        if update is None:
            break
        if update.message is None or update.message.text is None:
            continue
        text = update.message.text
        if text == '/start':
            await start(update, None)
        elif text == '/gas':
            await gas(update, None)
        elif text == '/subscribe':
            await subscribe(update, None)
        elif text == '/unsubscribe':
            await unsubscribe(update, None)
        elif text == '/thresholds':
            await thresholds(update, None)
        elif text.startswith('/set_thresholds'):
            await set_thresholds(update, None)
        elif text.startswith('/track'):
            await track(update, None)
        elif text in ('/help', '?'):
            await help_command(update, None)


async def subscribe(update, context):
    """Subscribe the user to gas price alerts."""
    chat_id = update.message.chat_id
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        await update.message.reply_text('You have subscribed to gas price alerts!')
    else:
        await update.message.reply_text('You are already subscribed.')


async def unsubscribe(update, context):
    """Unsubscribe the user from gas price alerts."""
    chat_id = update.message.chat_id
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        await update.message.reply_text('You have unsubscribed from gas price alerts.')
    else:
        await update.message.reply_text("You aren't subscribed.")


async def thresholds(update, context):
    """Get the current alert thresholds."""
    chat_id = update.message.chat_id
    current_thresholds = user_thresholds.get(
        chat_id, {"green": 30, "yellow": 35})
    text = (f"Current alert thresholds:\n"
            f"🟢 Green (Low): {current_thresholds['green']} gwei\n"
            f"🟡 Yellow (Medium): {current_thresholds['yellow']} gwei")
    await update.message.reply_text(text)


async def set_thresholds(update, context):
    """Set the alert thresholds."""
    chat_id = update.message.chat_id
    try:
        # Extract green and yellow thresholds from the message
        args = update.message.text.split()[1:]  # e.g., /set_thresholds 20 40
        green_threshold, yellow_threshold = map(int, args)

        # Update the user's thresholds
        user_thresholds[chat_id] = {
            "green": green_threshold, "yellow": yellow_threshold}
        text = ("Thresholds updated successfully:\n"
                f"🟢 Green (Low): {green_threshold} gwei\n"
                f"🟡 Yellow (Medium): {yellow_threshold} gwei")
    except (ValueError, IndexError):
        text = ("Invalid format. Use the command like this:\n"
                "/set_thresholds <green_threshold> <yellow_threshold>\n"
                "For example: /set_thresholds 20 40")
    await update.message.reply_text(text)


async def track(update, context):
    """Start temporary tracking for a specified duration."""
    chat_id = update.message.chat_id
    try:
        # Extract the duration from the message
        args = update.message.text.split()[1:]  # e.g., /track 5
        duration = int(args[0])  # Duration in minutes

        if 0 < duration <= 10:  # Ensure duration is between 1 and 10 minutes
            await update.message.reply_text(f"Starting temporary tracking for {duration} minutes.")
            await start_temporary_tracking(chat_id, duration)
        else:
            await update.message.reply_text("Invalid duration. Please specify a number between 1 and 10.")

    except (ValueError, IndexError):
        await update.message.reply_text("Invalid format. Use the command like this: /track <minutes>")


async def start_temporary_tracking(chat_id, duration):
    """Track gas prices and send updates every 30 seconds for a specified duration."""
    end_time = asyncio.get_event_loop().time() + duration * \
        60  # Convert minutes to seconds

    while asyncio.get_event_loop().time() < end_time:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ETHERSCAN_API_URL, timeout=60) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == "1":
                            result = data.get("result")
                            low_gas = int(result['SafeGasPrice'])
                            average_gas = int(result['ProposeGasPrice'])
                            fast_gas = int(result['FastGasPrice'])

                            # Get the user's custom thresholds or use default values
                            current_thresholds = user_thresholds.get(
                                chat_id, {"green": 30, "yellow": 35})

                            # Create the message text with the appropriate emojis
                            alert_text = (
                                f"🚀 Temporary Ethereum Gas Prices Update 🚀\n"
                                f"Low: {low_gas} gwei {'🟢' if low_gas <= current_thresholds['green'] else '🔴'}\n"
                                f"Average: {average_gas} gwei {'🟢' if average_gas <= current_thresholds['green'] else '🔴'}\n"
                                f"Fast: {fast_gas} gwei {'🟢' if fast_gas <= current_thresholds['green'] else '🔴'}"
                            )

                            # Send the current gas prices
                            try:
                                await bot.send_message(chat_id=chat_id, text=alert_text)
                            except aiohttp.ClientError as e:
                                logger.error(
                                    "Failed to send temporary tracking alert to %s: %s", chat_id, e)
                        else:
                            logger.error(
                                "Error fetching gas prices during temporary tracking.")
                    else:
                        logger.error(
                            "Failed to retrieve data during temporary tracking: %s", response.status)

            await asyncio.sleep(30)  # Update every 30 seconds

        except asyncio.CancelledError:
            # Handle the cancellation
            print(f"Temporary tracking for chat {chat_id} was cancelled")
            return  # Ensure immediate exit


async def monitor_gas_prices():
    """Monitor gas prices and send an alert when they are low."""
    print("Starting monitor_gas_prices task")  # Unique start log

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ETHERSCAN_API_URL, timeout=60) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == "1":
                            result = data.get("result")
                            new_low_gas = int(result['SafeGasPrice'])
                            new_average_gas = int(result['ProposeGasPrice'])
                            new_fast_gas = int(result['FastGasPrice'])

                            for chat_id in subscribers:
                                # Retrieve the last sent prices or use default thresholds
                                last_prices = last_sent_prices.get(
                                    chat_id, {"low": 0, "average": 0, "fast": 0})
                                current_thresholds = user_thresholds.get(
                                    chat_id, {"green": 30, "yellow": 35})

                                # Check if the price has changed significantly
                                if (abs(new_low_gas - last_prices["low"]) > UPDATE_THRESHOLD or
                                    abs(new_average_gas - last_prices["average"]) > UPDATE_THRESHOLD or
                                        abs(new_fast_gas - last_prices["fast"]) > UPDATE_THRESHOLD):

                                    # Update the last sent prices for this chat_id
                                    last_sent_prices[chat_id] = {
                                        "low": new_low_gas, "average": new_average_gas, "fast": new_fast_gas}

                                    # Prepare the alert text
                                    alert_text = (
                                        f"⚠️ Alert: Ethereum Gas Price Update! ⚠️\n"
                                        f"Low: {new_low_gas} gwei {'🟢' if new_low_gas <= current_thresholds['green'] else '🔴'}\n"
                                        f"Average: {new_average_gas} gwei {'🟢' if new_average_gas <= current_thresholds['green'] else '🔴'}\n"
                                        f"Fast: {new_fast_gas} gwei {'🟢' if new_fast_gas <= current_thresholds['green'] else '🔴'}"
                                    )

                                    # Send the alert to this subscriber
                                    try:
                                        await bot.send_message(chat_id=chat_id, text=alert_text)
                                    except aiohttp.ClientError as e:
                                        logger.error(
                                            "Failed to send alert to %s: %s", chat_id, e)
                                else:
                                    logger.info(
                                        "No significant price change for chat %s. No alert sent.", chat_id)

                    else:
                        logger.error(
                            "Failed to retrieve gas data: %s", response.status)

            # Wait for 60 seconds before checking again
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            # Handle the cancellation
            print("Gas monitor task was cancelled")
            return  # Ensure immediate exit


async def start(update, context):
    """Send a message when the command /start is issued."""
    start_text = (
        "Hi! I'm your Ethereum Gas Tracker Bot. Here are some commands you can use:\n"
        "/gas - Get the current Ethereum gas prices\n"
        "/subscribe - Subscribe to alerts\n"
        "/unsubscribe - Unsubscribe from alerts\n"
        "/thresholds - Get the current alert thresholds\n"
        "/set_thresholds - Set the alert thresholds\n"
        "/track - Start temporary tracking for a specified duration (max 10 minutes)\n"
        "Or simply send '?' for help."
    )
    await update.message.reply_text(start_text)


async def shutdown(sig: signal, server: Server, loop):
    """Clean up tasks and shut down the bot gracefully."""
    print(f"Received exit signal {sig.name}...")

    # Stop the updater if it's running
    if updater.running:
        await updater.stop()

    # Shut down the server if it's defined
    if server:
        server.should_exit = True

    # Cancel all outstanding tasks
    tasks = [t for t in asyncio.all_tasks(
        loop) if t is not asyncio.current_task()]

    print(f"Cancelling {len(tasks)} outstanding tasks")

    for task in tasks:
        # Log the task being cancelled
        print(f"Cancelling task: {task.get_name()}")
        task.cancel()
        try:
            await task  # Wait for the task to be cancelled
        except asyncio.CancelledError:
            pass  # Task cancellation is expected

    print("All tasks have been cancelled")

    # Wait for all tasks to be cancelled
    await asyncio.gather(*tasks, return_exceptions=True)

    loop.stop()
    print("Shutdown complete")


async def main():
    """Start the bot and the gas price monitor."""
    loop = asyncio.get_running_loop()

    # Configure Uvicorn server
    config = Config(app=app, host="0.0.0.0", port=8000, loop=loop)
    server = Server(config)

    # Handle shutdown signals
    signals = (signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(s, server,  loop)))

    def run_server():
        """Run the Uvicorn server in a separate thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())
        loop.close()

    try:
        async with updater:
            # Tasks to run in parallel
            tasks = [
                asyncio.create_task(updater.start_polling(), name="updater"),
                asyncio.create_task(monitor_gas_prices(), name="gas_monitor"),
                asyncio.create_task(handle_updates(
                    update_queue), name="update_handler")
            ]

            # Run the Uvicorn server in a separate thread
            server_thread = Thread(target=run_server)
            server_thread.start()

            try:
                # Wait for all tasks to complete (they won't unless canceled)
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                # Handle the cancellation of the asyncio.gather
                print("Main tasks were cancelled")
    except asyncio.CancelledError:
        logger.info(
            "CancelledError caught in main() - during updater operation")

    # Ensure the server thread stops when the main tasks are cancelled
    server_thread.join()

if __name__ == '__main__':
    asyncio.run(main())
