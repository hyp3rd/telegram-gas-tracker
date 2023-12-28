"""API controller for the Telegram bot."""

import httpx
from fastapi import FastAPI, HTTPException
from telegram import Bot
from telegram.error import TelegramError

from config import ConfigHandler

app = FastAPI()

config = ConfigHandler()


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    results = {"telegram_api": False, "external_resources": {}}

    # Check Telegram Bot API connectivity
    try:
        bot = Bot(token=config.telegram_token())
        bot_info = await bot.get_me()
        results["telegram_api"] = bot_info is not None
    except TelegramError:
        results["telegram_api"] = False

    # Check Etherscan API health
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                config.etherscan_api_url(),
                params={"module": "stats", "action": "ping"},
                timeout=5,
            )
            results["etherscan_api"] = (
                response.status_code == 200 and response.json().get("message") == "OK"
            )
    except httpx.RequestError:
        results["etherscan_api"] = False

    # Overall health status
    if all(results.values()):
        return {"status": "healthy", "details": results}

    raise HTTPException(
        status_code=503, detail={"status": "unhealthy", "details": results}
    )
