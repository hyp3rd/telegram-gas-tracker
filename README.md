# Ethereum Gas Tracker Bot

[![Pylint](https://github.com/hyp3rd/telegram-gas-tracker/actions/workflows/pylint.yml/badge.svg)](https://github.com/hyp3rd/telegram-gas-tracker/actions/workflows/pylint.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A simple Telegram bot that tracks Ethereum gas prices using the Etherscan API.

## Features

- Check current Ethereum gas prices.
- Subscribe to alerts for low gas prices.
- Customize gas price alert thresholds.
- Track the gas price for a specific amount of minutes.
- API with health check endpoint.

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
4. Run the bot: `python tracker.py`

## Usage

### Commands

- `/start` - Start the bot.
- `/help` - Show help.
- `/gas` - Show current gas prices.
- `/subscribe` - Subscribe to gas price alerts.
- `/unsubscribe` - Unsubscribe from gas price alerts.
- `/thresholds` - Show current gas price alert thresholds.
- `/set_thresholds` - Set gas price alert thresholds.
- `/track` - Track the gas price for a specific amount of minutes (max 10).

### Alerts

The bot will send a message to all subscribers when the gas price falls below the `low` threshold or rises above the `high` threshold.

## License

This project is licensed under the [MIT License](LICENSE).

## Code of Conduct

This project is governed by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).

## Disclaimer

This project is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose and noninfringement. In no event shall the author(s) or the project be liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from, out of or in connection with the project or the use or other dealings in the project. Use at your own risk.

## Author

I'm a surfer, a trader, and a software architect with 15 years of experience designing highly available distributed production environments and developing cloud-native apps in public and private clouds. Just your average bloke. Feel free to connect with me on LinkedIn, but no funny business.

[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/francesco-cosentino/)
