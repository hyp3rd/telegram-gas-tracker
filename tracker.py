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

# from enums import Env
from gas_tracker import GasTracker, TrackerState
from release import __version__ as version
from tracker.config import ConfigHandler
from tracker.logger import Logger
from wallet_tracker import WalletTracker


class Tracker(metaclass=SingletonMeta):
    """Tracker class."""

    def __init__(self, config: ConfigHandler):
        """Initialize the Tracker class."""

        self.config = config
        self.logger = Logger.init_logger("tracker")
        # Enable logging
        self.logger.configure()
        self.logger.info("Tracker v%s", version)
        self.logger.info("Logger configured")
        self.logger.info("Tracker running in %s environment", config._ennvironment)

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

    async def __set_menu_button(self, chat_id=None):  # pylint: disable=too-many-locals
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
            untrack_wallet_command = BotCommand(
                command="untrack_wallet", description="Untrack a wallet"
            )
            list_tracked_wallets_command = BotCommand(
                command="list_tracked_wallets", description="List the tracked wallets"
            )
            resolved_wallet_command = BotCommand(
                command="resolve_wallet", description="Resolve a wallet"
            )
            pause_tracking_wallet_command = BotCommand(
                command="pause_tracking_wallet", description="Pause tracking a wallet"
            )
            resume_tracking_wallet_command = BotCommand(
                command="resume_tracking_wallet", description="Resume tracking a wallet"
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
                    untrack_wallet_command,
                    list_tracked_wallets_command,
                    resolved_wallet_command,
                    pause_tracking_wallet_command,
                    resume_tracking_wallet_command,
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
            "/track_wallet - Track a wallet for new transactions\n"
            "/untrack_wallet - Untrack a wallet\n"
            "/list_tracked_wallets - List the tracked wallets\n"
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
                    TrackerState.TRACKING.value: [
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
                    TrackerState.THRESHOLDS.value: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            gas_tracker.received_thresholds,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            # Define conversation handler for '/track_wallet' command
            track_wallet_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("track_wallet", wallet_tracker.ask_for_wallet)
                ],
                states={
                    TrackerState.WALLET_ADDRESS.value: [  # Ensure this state is defined in TrackerState
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.ask_for_wallet_tag,
                        )
                    ],
                    TrackerState.WALLET_TAG.value: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.received_wallet,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            # Define conversation handler for '/untrack_wallet' command
            untrack_wallet_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler(
                        "untrack_wallet", wallet_tracker.ask_for_wallet_untrack
                    )
                ],
                states={
                    TrackerState.WALLET_UNTRACKED.value: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.received_wallet_untrack,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            # Define conversation handler for '/resolve_wallet' command
            resolve_wallet_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler(
                        "resolve_wallet", wallet_tracker.ask_for_wallet_to_resolve
                    )
                ],
                states={
                    TrackerState.WALLET_RESOLVED.value: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.received_wallet_to_resolve,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            pause_tracking_wallet_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler(
                        "pause_tracking_wallet", wallet_tracker.ask_for_wallet_to_pause
                    )
                ],
                states={
                    TrackerState.WALLET_PAUSE.value: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.received_wallet_to_pause,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )
            resume_tracking_wallet_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler(
                        "resume_tracking_wallet",
                        wallet_tracker.ask_for_wallet_to_resume,
                    )
                ],
                states={
                    TrackerState.WALLET_UNPAUSE.value: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            wallet_tracker.received_wallet_to_resume,
                        )
                    ],
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
            )

            # Add conversation handlers to the application
            self.application.add_handler(track_wallet_conv_handler)
            self.application.add_handler(untrack_wallet_conv_handler)
            self.application.add_handler(resolve_wallet_conv_handler)
            self.application.add_handler(pause_tracking_wallet_conv_handler)
            self.application.add_handler(resume_tracking_wallet_conv_handler)

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
            self.application.add_handler(
                CommandHandler(
                    "list_tracked_wallets", wallet_tracker.list_tracked_wallets
                )
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
                asyncio.create_task(wallet_tracker.refresh_db_cache())
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
                await update.message.reply_text("An error occured. Please try again.")
            except Exception as e:  # pylint: disable=broad-except
                self.logger.error("Error while sending error message to user: %s", e)

        return ConversationHandler.END


if __name__ == "__main__":
    __config = ConfigHandler()
    __config.bootstrap()
    tracker = Tracker(__config)

    asyncio.run(tracker.main())
