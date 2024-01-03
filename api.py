"""API controller for the Telegram bot."""

import httpx
from fastapi import FastAPI, HTTPException
from telegram import Bot
from telegram.error import TelegramError

from release import __version__ as version
from tracker.config import ConfigHandler

app = FastAPI()

config = ConfigHandler()


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    results = {}
    results["version"] = version

    # Check Telegram Bot API connectivity
    try:
        bot = Bot(token=config.telegram_token)
        bot_info = await bot.get_me()
        results["telegram_api"] = bot_info is not None
    except TelegramError:
        results["telegram_api"] = False

    # Check Etherscan API health
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                config.etherscan_api_url,
                params={"module": "stats", "action": "ping"},
                timeout=5,
            )
            results["etherscan_api"] = response.status_code == 200

    except httpx.RequestError:
        results["etherscan_api"] = False

    # Determine the overall health status based on individual checks
    overall_health = all(status is True for status in results.values())

    if overall_health:
        return {"status": "healthy", "details": results}

    raise HTTPException(
        status_code=503, detail={"status": "unhealthy", "details": results}
    )
