"""
main.py — Jalankan Telegram bot + Flask API secara bersamaan.
Bot berjalan di thread background dengan event loop sendiri.
Flask di thread utama untuk Render health check.
"""
import os
import threading
import logging
import asyncio

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)


def run_bot():
    """Jalankan Telegram bot di thread terpisah dengan event loop sendiri."""
    import bot as bot_module
    # Python 3.10+ tidak punya current event loop di thread baru
    # Harus buat loop baru secara eksplisit
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("[MAIN] Starting Telegram bot thread dengan event loop baru...")
    try:
        bot_module.main()
    except Exception as e:
        logger.error(f"[MAIN] Bot thread error: {e}", exc_info=True)


def run_api():
    """Jalankan Flask API di thread utama."""
    from api import app
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", 5000)))
    logger.info(f"[MAIN] Starting Flask API on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Telegram bot di background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    bot_thread.start()

    # Flask API di main thread
    run_api()
