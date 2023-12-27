"""API controller for the Telegram bot."""
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
