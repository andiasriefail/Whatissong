"""
main.py — Jalankan Telegram bot + Flask API secara bersamaan.
Bot berjalan di thread background, Flask di thread utama (untuk Render health check).
"""
import os
import threading
import logging
import asyncio

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def run_bot():
    """Jalankan Telegram bot di thread terpisah."""
    import bot as bot_module
    logger.info("Starting Telegram bot thread...")
    bot_module.main()


def run_api():
    """Jalankan Flask API di thread utama."""
    from api import app
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", 5000)))
    logger.info(f"Starting Flask API on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Telegram bot di background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    bot_thread.start()

    # Flask API di main thread (Render mendeteksi port dari sini)
    run_api()
