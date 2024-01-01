"""Simple Bot to track Ethereum gas prices on Etherscan"""
import asyncio
from asyncio.queues import Queue

import aiohttp
import telegram.error as telegram_error
from telegram import BotCommand, MenuButtonCommands, Update
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    ExtBot,
    InvalidCallbackData,
    MessageHandler,
    Updater,
    filters,
)
from uvicorn import Config, Server

from api import app
from core import SingletonMeta
from gas_tracker import AwaitInterval, GasTracker
from tracker.config import ConfigHandler
from tracker.logger import Logger
from wallet_tracker import WalletTracker

config = ConfigHandler()


class Tracker(metaclass=SingletonMeta):
    """Tracker class."""

    def __init__(self):
        """Initialize the Tracker class."""
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
                    bot=ExtBot(config.telegram_token),
                    update_queue=self.update_queue,
                )
            )
            .build()
        )

        self.tracked_wallets = {}  # {wallet_address: (chat_id, last_checked_block)}

    async def start(self, update, context):
        """Send a message when the command /start is issued."""
        await self.help_command(update, context)

    async def __set_menu_button(self, chat_id=None):
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
            track_wallet_command = BotCommand(
                command="track_wallet", description="Track a wallet"
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
                    track_wallet_command,
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

            gas_tracker = GasTracker(application=self.application, logger=self.logger)
            wallet_tracker = WalletTracker(
                application=self.application, logger=self.logger
            )

            # Define conversation handler for '/track' command
            track_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("track", gas_tracker.ask_for_tracking_duration)
                ],
                states={
                    AwaitInterval.TRACKING: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            gas_tracker.received_tracking_duration,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            # Define conversation handler for '/set_thresholds' command
            thresholds_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("set_thresholds", gas_tracker.ask_for_thresholds)
                ],
                states={
                    AwaitInterval.THRESHOLDS: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            gas_tracker.received_thresholds,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            # Define conversation handler for '/track_wallet' command
            wallet_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("track_wallet", wallet_tracker.ask_for_wallet)
                ],
                states={
                    AwaitInterval.WALLET_ADDRESS: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.received_wallet,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            self.application.add_handler(wallet_conv_handler)

            # Add conversation handlers to the application
            self.application.add_handler(track_conv_handler)
            self.application.add_handler(thresholds_conv_handler)

            # Add handlers for Telegram commands
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("start", self.start))
            self.application.add_handler(CommandHandler("gas", gas_tracker.gas))
            self.application.add_handler(
                CommandHandler("subscribe", gas_tracker.subscribe)
            )
            self.application.add_handler(
                CommandHandler("unsubscribe", gas_tracker.unsubscribe)
            )
            self.application.add_handler(
                CommandHandler("thresholds", gas_tracker.thresholds)
            )
            self.application.add_error_handler(self.error_handler)

            self.logger.info("Handlers initialized")

            await self.__set_menu_button()

            # Run application and webserver together
            async with self.application.updater:
                self.logger.info("Starting the bot and monitors")
                await self.application.start()
                asyncio.create_task(gas_tracker.monitor_gas_prices())
                asyncio.create_task(wallet_tracker.monitor_wallet_transactions())
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
