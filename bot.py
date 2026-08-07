import os
import re
import logging
import requests
import tempfile
import subprocess
import traceback
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    filters, ContextTypes
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

AUDD_API_KEYS = [
    os.environ.get("AUDD_API_KEY_1", ""),
    os.environ.get("AUDD_API_KEY_2", ""),
    os.environ.get("AUDD_API_KEY_3", ""),
]
AUDD_API_KEYS = [k for k in AUDD_API_KEYS if k]

UNSUPPORTED_URLS = [
    r"(youtube\.com|youtu\.be)",
    r"twitch\.tv",
]

BOT_USERNAME: str | None = None


def is_unsupported_url(url: str) -> bool:
    for pattern in UNSUPPORTED_URLS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def is_valid_url(text: str) -> str | None:
    pattern = r'https?://[^\s]+'
    match = re.search(pattern, text)
    return match.group(0) if match else None


def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except:
        return -1


def convert_ogg_opus_to_mp3(input_path: str) -> str:
    output_path = input_path + ".mp3"
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-acodec", "libopus", "-i", input_path,
             "-ar", "48000", "-ac", "1", "-b:a", "128k", output_path],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return output_path
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-ar", "48000", "-ac", "1", "-b:a", "128k", output_path],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return output_path
    except Exception:
        pass

    return input_path


def recognize_with_audd(file_path: str = None, url: str = None):
    if not AUDD_API_KEYS:
        logger.error("[AUDD] Tidak ada API key tersedia")
        return False

    for i, api_key in enumerate(AUDD_API_KEYS):
        try:
            data = {"api_token": api_key, "return": "spotify,apple_music"}

            if url:
                data["url"] = url
                response = requests.post("https://api.audd.io/", data=data, timeout=30)
            else:
                with open(file_path, "rb") as f:
                    response = requests.post(
                        "https://api.audd.io/",
                        data=data,
                        files={"file": (os.path.basename(file_path), f)},
                        timeout=30
                    )

            result_data = response.json()
            status = result_data.get("status")

            if status == "success":
                result = result_data.get("result")
                if result:
                    logger.info(f"[AUDD] Lagu ditemukan: {result.get('artist')} — {result.get('title')}")
                return result

            error = result_data.get("error", {})
            error_code = error.get("error_code") if isinstance(error, dict) else None

            if error_code in (900, 901):
                logger.warning(f"[AUDD] Key #{i+1} habis kuota, coba key berikutnya")
                continue

            logger.error(f"[AUDD] Error: {error}")
            return None

        except requests.exceptions.Timeout:
            logger.error(f"[AUDD] Timeout pada key #{i+1}")
            continue
        except Exception as e:
            logger.error(f"[AUDD] Exception pada key #{i+1}: {e}")
            continue

    logger.error("[AUDD] Semua API key gagal")
    return False


def get_spotify_url(result: dict):
    try:
        return result["spotify"]["external_urls"]["spotify"]
    except:
        return None


def get_cover_url(result: dict):
    try:
        url = result["apple_music"]["artwork"]["url"]
        return url.replace("{w}", "500").replace("{h}", "500")
    except:
        return None


def get_youtube_search(artist: str, title: str) -> str:
    query = requests.utils.quote(f"{artist} {title}")
    return f"https://www.youtube.com/results?search_query={query}"


def get_lyrics(artist: str, title: str):
    try:
        url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(title)}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            lyrics = resp.json().get("lyrics", "").strip()
            if lyrics:
                return lyrics[:3000] + ("\n\n... (truncated)" if len(lyrics) > 3000 else "")
    except Exception:
        pass
    return None


def get_deezer_preview(artist: str, title: str):
    try:
        query = requests.utils.quote(f"{artist} {title}")
        resp = requests.get(f"https://api.deezer.com/search?q={query}&limit=1", timeout=10)
        if resp.status_code == 200:
            tracks = resp.json().get("data", [])
            if tracks:
                return tracks[0].get("preview")
    except Exception:
        pass
    return None


def build_result_payload(result: dict) -> dict:
    title  = result.get("title", "Unknown")
    artist = result.get("artist", "Unknown")
    album  = result.get("album", "")
    return {
        "title":       title,
        "artist":      artist,
        "album":       album,
        "cover_url":   get_cover_url(result),
        "spotify_url": get_spotify_url(result),
        "youtube_url": get_youtube_search(artist, title),
        "lyrics":      get_lyrics(artist, title),
        "preview_url": get_deezer_preview(artist, title),
    }


def is_group_or_channel(update: Update) -> bool:
    chat_type = update.effective_chat.type if update.effective_chat else None
    return chat_type in ("group", "supergroup", "channel")


def is_mentioned(update: Update, bot_username: str | None) -> bool:
    message = update.effective_message
    if not message:
        return False
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.is_bot:
            if bot_username and message.reply_to_message.from_user.username:
                if message.reply_to_message.from_user.username.lower() == bot_username.lower():
                    return True
    if not bot_username:
        return False
    mention_text = f"@{bot_username}".lower()
    text = message.text or message.caption or ""
    if mention_text in text.lower():
        return True
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == "mention":
            entity_text = text[entity.offset: entity.offset + entity.length]
            if entity_text.lower() == mention_text:
                return True
    return False


def strip_mention(text: str, bot_username: str | None) -> str:
    if not text or not bot_username:
        return text
    return re.sub(rf"@{re.escape(bot_username)}", "", text, flags=re.IGNORECASE).strip()


async def send_result_telegram(message, result: dict):
    title       = result["title"]
    artist      = result["artist"]
    cover_url   = result["cover_url"]
    spotify_url = result["spotify_url"]
    youtube_url = result["youtube_url"]
    lyrics      = result["lyrics"]
    preview_url = result["preview_url"]

    caption = f"🎵 *{title}*\n👤 {artist}"
    buttons = []
    if spotify_url:
        buttons.append(InlineKeyboardButton("🟢 Spotify", url=spotify_url))
    buttons.append(InlineKeyboardButton("🔴 YouTube", url=youtube_url))
    keyboard = InlineKeyboardMarkup([buttons])

    if cover_url:
        await message.reply_photo(photo=cover_url, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.reply_text(caption, parse_mode="Markdown", reply_markup=keyboard)

    if preview_url:
        await message.reply_audio(audio=preview_url, title=title, performer=artist)

    if lyrics:
        await message.reply_text(f"📝 *Lyrics — {title}:*\n\n{lyrics}", parse_mode="Markdown")
    else:
        await message.reply_text("_(Lyrics not found)_", parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 *What Song Is This?*\n\n"
        "Send me a:\n"
        "• 🎤 Voice note\n"
        "• 🎵 Audio file (mp3, etc)\n"
        "• 🔗 URL to an audio/video file\n\n"
        "I'll find the title, artist, lyrics, and links to Spotify & YouTube!\n\n"
        "📌 *In groups/channels* — mention me or reply to my message along with the audio/URL.\n\n"
        "⚠️ _YouTube & Twitch links are not supported._\n"
        "⚠️ _DJ remixes or mashups may not be recognized._",
        parse_mode="Markdown",
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if is_group_or_channel(update) and not is_mentioned(update, BOT_USERNAME):
        return

    if message.voice:
        file = await message.voice.get_file()
        ext  = ".ogg"
    elif message.audio:
        file = await message.audio.get_file()
        ext  = ".mp3"
    else:
        await message.reply_text("Please send a voice note or audio file! 🎵")
        return

    loading_msg = await message.reply_text("🎧 Listening... hang on!")
    tmp_path = converted_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name

        await file.download_to_drive(tmp_path)

        if ext == ".ogg":
            converted_path = convert_ogg_opus_to_mp3(tmp_path)
        else:
            converted_path = tmp_path

        result = recognize_with_audd(file_path=converted_path)

        if result is False:
            await loading_msg.edit_text("⏳ Please try again in a moment!")
            return
        if result is None:
            await loading_msg.edit_text("❌ Song not recognized. Try a longer or clearer clip!")
            return

        await loading_msg.delete()
        await send_result_telegram(message, build_result_payload(result))

    except Exception as e:
        logger.error(f"[AUDIO] Error: {e}\n{traceback.format_exc()}")
        await loading_msg.edit_text("⏳ Please try again in a moment!")
    finally:
        for path in set(filter(None, [tmp_path, converted_path])):
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if is_group_or_channel(update) and not is_mentioned(update, BOT_USERNAME):
        return

    raw_text = (message.text or message.caption or "").strip()
    text = strip_mention(raw_text, BOT_USERNAME)
    url  = is_valid_url(text)

    if not url:
        return

    if is_unsupported_url(url):
        await message.reply_text("⚠️ YouTube dan Twitch tidak support.\n\nKirim link langsung ke file audio/video atau pakai voice note!")
        return

    loading_msg = await message.reply_text("🎧 Listening... hang on!")
    try:
        result = recognize_with_audd(url=url)
        if result is False:
            await loading_msg.edit_text("⏳ Please try again in a moment!")
            return
        if result is None:
            await loading_msg.edit_text("❌ Song not recognized. The URL may not contain music.")
            return
        await loading_msg.delete()
        await send_result_telegram(message, build_result_payload(result))
    except Exception as e:
        logger.error(f"[URL] Error: {e}\n{traceback.format_exc()}")
        await loading_msg.edit_text("⏳ Please try again in a moment!")


def main():
    global BOT_USERNAME

    logger.info("[STARTUP] WhatSongLyricsIsThis Bot starting...")
    logger.info(f"[STARTUP] AUDD keys terdaftar: {len(AUDD_API_KEYS)}")

    async def post_init(application):
        global BOT_USERNAME
        bot_info = await application.bot.get_me()
        BOT_USERNAME = bot_info.username
        logger.info(f"[STARTUP] Bot: @{BOT_USERNAME}")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_url))

    logger.info("[STARTUP] Bot siap!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
