"""API controller for the Telegram bot."""
import os

import httpx
from fastapi import FastAPI, HTTPException
from telegram import Bot
from telegram.error import TelegramError

app = FastAPI()

# Assuming these are defined somewhere in your application
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe"

# You can add more service-specific URLs or checks here
EXTERNAL_RESOURCES = [
    "https://api.etherscan.io/api",
    TELEGRAM_API_URL  # To check if the bot is still connected to Telegram API
]


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    results = {
        "telegram_api": False,
        "external_resources": {}
    }

    # Check Telegram Bot API connectivity
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot_info = await bot.get_me()
        results["telegram_api"] = bot_info is not None
    except TelegramError:
        results["telegram_api"] = False

    # Check external resources connectivity
    async with httpx.AsyncClient() as client:
        for url in EXTERNAL_RESOURCES:
            try:
                response = await client.get(url, timeout=5)
                results["external_resources"][url] = response.status_code == 200
            except httpx.RequestError:
                results["external_resources"][url] = False

    if all(results["external_resources"].values()) and results["telegram_api"]:
        return {"status": "healthy", "details": results}

    raise HTTPException(status_code=503, detail={
                        "status": "unhealthy", "details": results})
