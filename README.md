# Ethereum Gas Tracker Bot

A simple Telegram bot that tracks Ethereum gas prices using the Etherscan API.

## Features

- Check current Ethereum gas prices.
- Subscribe to alerts for low gas prices.
- Customize gas price alert thresholds.

## Requirements

- Python 3.10 or later.
- A Telegram bot token (obtainable through [BotFather](https://t.me/botfather)).
- An Etherscan API key (obtainable from [Etherscan.io](https://etherscan.io/)).
- Docker (optional).

## Installation

### Docker

1. Clone this repository.
2. Copy `.env-example` to `.env` and fill in the required values.
3. Build the Docker image: `docker build -t gas-tracker-bot .`
4. Run the Docker container: `docker run -d -e TELEGRAM_TOKEN=your_token -e ETHERSCAN_API_KEY=your_api_key --name gas-tracker-bot gas-tracker-bot`

### Manual

1. Clone this repository.
2. Copy `.env-example` to `.env` and fill in the required values.
3. Install the required Python packages: `pip install -r requirements.txt`
4. Run the bot: `python main.py`

## Usage

### Commands

- `/start` - Start the bot.
- `/help` - Show help.
- `/gas` - Show current gas prices.
- `/subscribe` - Subscribe to gas price alerts.
- `/unsubscribe` - Unsubscribe from gas price alerts.
- `/thresholds` - Show current gas price alert thresholds.
- `/set_thresholds` - Set gas price alert thresholds.

### Alerts

The bot will send a message to all subscribers when the gas price falls below the `low` threshold or rises above the `high` threshold.

## License

This project is licensed under the [MIT License](LICENSE).

## Code of Conduct

This project is governed by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
