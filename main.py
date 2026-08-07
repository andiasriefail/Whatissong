"""
main.py — Flask di background thread, Telegram bot di main thread.
run_polling() wajib di main thread karena butuh OS signal handler.
Render health check tetap jalan karena Flask di thread terpisah.
"""
import os
import time
import threading
import logging

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)


def run_api():
    """Flask API di background thread."""
    from api import app
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", 5000)))
    logger.info(f"[MAIN] Flask API starting on port {port}...")
    # use_reloader=False wajib di thread non-main
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    # 1. Start Flask di background thread
    api_thread = threading.Thread(target=run_api, daemon=True, name="flask-api")
    api_thread.start()

    # 2. Tunggu Flask benar-benar listening (Render health check butuh ini)
    time.sleep(3)
    logger.info("[MAIN] Flask API thread started, launching Telegram bot on main thread...")

    # 3. Telegram bot di main thread — satu-satunya cara agar signal handler works
    import bot as bot_module
    bot_module.main()
