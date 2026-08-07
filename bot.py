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
    level=logging.DEBUG
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


def check_ffmpeg():
    """Cek apakah ffmpeg tersedia dan versinya."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else "no output"
        logger.info(f"[FFMPEG] Tersedia: {first_line}")
        return True
    except FileNotFoundError:
        logger.error("[FFMPEG] ❌ TIDAK DITEMUKAN di sistem! VN tidak bisa dikonversi.")
        return False
    except Exception as e:
        logger.error(f"[FFMPEG] Error saat cek: {e}")
        return False


def probe_audio_file(path: str):
    """Log info file audio menggunakan ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True
        )
        if result.stdout:
            logger.debug(f"[FFPROBE] Info file {path}:\n{result.stdout}")
        else:
            logger.warning(f"[FFPROBE] Tidak ada output untuk {path}. stderr: {result.stderr}")
    except FileNotFoundError:
        logger.warning("[FFPROBE] ffprobe tidak tersedia, skip probe.")
    except Exception as e:
        logger.warning(f"[FFPROBE] Error: {e}")


def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except:
        return -1


def convert_ogg_opus_to_mp3(input_path: str) -> str:
    output_path = input_path + ".mp3"
    size_before = get_file_size(input_path)
    logger.info(f"[CONVERT] Mulai konversi: {input_path} ({size_before} bytes)")

    # Probe dulu sebelum konversi
    probe_audio_file(input_path)

    # Attempt 1: explicit libopus decoder
    logger.debug("[CONVERT] Attempt 1: -acodec libopus")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-acodec", "libopus", "-i", input_path,
             "-ar", "48000", "-ac", "1", "-b:a", "128k", output_path],
            capture_output=True, text=True
        )
        logger.debug(f"[CONVERT] Attempt 1 stdout: {result.stdout}")
        logger.debug(f"[CONVERT] Attempt 1 stderr: {result.stderr}")
        if result.returncode == 0:
            size_after = get_file_size(output_path)
            logger.info(f"[CONVERT] ✅ Attempt 1 berhasil → {output_path} ({size_after} bytes)")
            return output_path
        else:
            logger.warning(f"[CONVERT] Attempt 1 gagal (returncode={result.returncode})")
    except Exception as e:
        logger.warning(f"[CONVERT] Attempt 1 exception: {e}")

    # Attempt 2: auto-detect codec
    logger.debug("[CONVERT] Attempt 2: auto-detect codec")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-ar", "48000", "-ac", "1", "-b:a", "128k", output_path],
            capture_output=True, text=True
        )
        logger.debug(f"[CONVERT] Attempt 2 stdout: {result.stdout}")
        logger.debug(f"[CONVERT] Attempt 2 stderr: {result.stderr}")
        if result.returncode == 0:
            size_after = get_file_size(output_path)
            logger.info(f"[CONVERT] ✅ Attempt 2 berhasil → {output_path} ({size_after} bytes)")
            return output_path
        else:
            logger.warning(f"[CONVERT] Attempt 2 gagal (returncode={result.returncode})")
    except Exception as e:
        logger.warning(f"[CONVERT] Attempt 2 exception: {e}")

    # Attempt 3: kirim file original tanpa konversi (last resort)
    logger.warning(f"[CONVERT] ⚠️ Semua konversi gagal, kirim file original: {input_path}")
    return input_path


def recognize_with_audd(file_path: str = None, url: str = None):
    logger.info(f"[AUDD] Mulai recognize — file_path={file_path}, url={url}")

    if not AUDD_API_KEYS:
        logger.error("[AUDD] ❌ Tidak ada API key AudD yang tersedia!")
        return False

    logger.info(f"[AUDD] Jumlah API key tersedia: {len(AUDD_API_KEYS)}")

    if file_path:
        size = get_file_size(file_path)
        logger.info(f"[AUDD] Ukuran file yang akan dikirim: {size} bytes")
        if size <= 0:
            logger.error(f"[AUDD] ❌ File kosong atau tidak ditemukan: {file_path}")
            return None
        # Probe file yang dikirim ke AudD
        probe_audio_file(file_path)

    for i, api_key in enumerate(AUDD_API_KEYS):
        logger.info(f"[AUDD] Mencoba key #{i+1}...")
        try:
            data = {"api_token": api_key, "return": "spotify,apple_music"}

            if url:
                data["url"] = url
                logger.debug(f"[AUDD] POST ke api.audd.io dengan URL: {url}")
                response = requests.post("https://api.audd.io/", data=data, timeout=30)
            else:
                logger.debug(f"[AUDD] POST ke api.audd.io dengan file: {file_path}")
                with open(file_path, "rb") as f:
                    response = requests.post(
                        "https://api.audd.io/",
                        data=data,
                        files={"file": (os.path.basename(file_path), f)},
                        timeout=30
                    )

            logger.info(f"[AUDD] Response HTTP status: {response.status_code}")
            logger.debug(f"[AUDD] Response headers: {dict(response.headers)}")

            try:
                result_data = response.json()
            except Exception as je:
                logger.error(f"[AUDD] ❌ Gagal parse JSON response: {je}")
                logger.error(f"[AUDD] Raw response text: {response.text[:500]}")
                continue

            logger.info(f"[AUDD] Response JSON: {result_data}")

            status = result_data.get("status")
            logger.info(f"[AUDD] Status: {status}")

            if status == "success":
                result = result_data.get("result")
                if result:
                    logger.info(f"[AUDD] ✅ Lagu ditemukan: {result.get('artist')} — {result.get('title')}")
                else:
                    logger.info("[AUDD] Status success tapi result null (lagu tidak dikenali)")
                return result

            # Handle error dari AudD
            error = result_data.get("error", {})
            logger.warning(f"[AUDD] Error dari AudD: {error}")
            error_code = error.get("error_code") if isinstance(error, dict) else None
            error_msg  = error.get("error_message", "") if isinstance(error, dict) else str(error)
            logger.warning(f"[AUDD] error_code={error_code}, message={error_msg}")

            if error_code in (900, 901):
                logger.warning(f"[AUDD] Key #{i+1} habis kuota (error {error_code}), coba key berikutnya...")
                continue

            logger.error(f"[AUDD] Error tidak bisa di-retry: {error_code} — {error_msg}")
            return None

        except requests.exceptions.Timeout:
            logger.error(f"[AUDD] ❌ Timeout pada key #{i+1}")
            continue
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[AUDD] ❌ Connection error pada key #{i+1}: {e}")
            continue
        except Exception as e:
            logger.error(f"[AUDD] ❌ Exception pada key #{i+1}: {e}\n{traceback.format_exc()}")
            continue

    logger.error("[AUDD] ❌ Semua API key gagal/habis kuota")
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
    logger.debug(f"[LYRICS] Mencari lirik: {artist} — {title}")
    try:
        url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(title)}"
        resp = requests.get(url, timeout=10)
        logger.debug(f"[LYRICS] Response status: {resp.status_code}")
        if resp.status_code == 200:
            lyrics = resp.json().get("lyrics", "").strip()
            if lyrics:
                logger.info(f"[LYRICS] ✅ Lirik ditemukan ({len(lyrics)} karakter)")
                return lyrics[:3000] + ("\n\n... (truncated)" if len(lyrics) > 3000 else "")
            else:
                logger.info("[LYRICS] Response 200 tapi lirik kosong")
        else:
            logger.warning(f"[LYRICS] Status bukan 200: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        logger.error(f"[LYRICS] Error: {e}")
    return None


def build_result_payload(result: dict) -> dict:
    title  = result.get("title", "Unknown")
    artist = result.get("artist", "Unknown")
    album  = result.get("album", "")
    logger.debug(f"[PAYLOAD] Build payload untuk: {artist} — {title} ({album})")
    return {
        "title":       title,
        "artist":      artist,
        "album":       album,
        "cover_url":   get_cover_url(result),
        "spotify_url": get_spotify_url(result),
        "youtube_url": get_youtube_search(artist, title),
        "lyrics":      get_lyrics(artist, title),
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

    logger.debug(f"[SEND] Mengirim hasil: cover={cover_url}, spotify={spotify_url}")

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
        "⚠️ _YouTube & Twitch links are not supported._",
        parse_mode="Markdown",
    )


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    logger.info(f"[AUDIO] Pesan masuk dari user={user.id} (@{user.username}), chat={chat.id} (type={chat.type})")

    if is_group_or_channel(update) and not is_mentioned(update, BOT_USERNAME):
        logger.debug("[AUDIO] Di grup/channel tapi tidak di-mention, skip.")
        return

    if message.voice:
        file = await message.voice.get_file()
        ext  = ".ogg"
        duration = message.voice.duration
        mime = message.voice.mime_type
        file_size = message.voice.file_size
        logger.info(f"[AUDIO] Voice note: file_id={message.voice.file_id}, duration={duration}s, mime={mime}, size={file_size} bytes")
    elif message.audio:
        file = await message.audio.get_file()
        ext  = ".mp3"
        duration = message.audio.duration
        mime = message.audio.mime_type
        file_size = message.audio.file_size
        logger.info(f"[AUDIO] Audio file: file_id={message.audio.file_id}, duration={duration}s, mime={mime}, size={file_size} bytes, title={message.audio.title}, performer={message.audio.performer}")
    else:
        logger.warning("[AUDIO] Pesan tidak punya voice/audio, skip.")
        await message.reply_text("Please send a voice note or audio file! 🎵")
        return

    logger.info(f"[AUDIO] File Telegram URL: {file.file_path}")

    loading_msg = await message.reply_text("🎧 Listening... hang on!")
    tmp_path = converted_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name

        logger.info(f"[AUDIO] Download file ke: {tmp_path}")
        await file.download_to_drive(tmp_path)
        size_downloaded = get_file_size(tmp_path)
        logger.info(f"[AUDIO] Download selesai, ukuran: {size_downloaded} bytes")

        if ext == ".ogg":
            logger.info("[AUDIO] Format OGG terdeteksi, mulai konversi ke MP3...")
            converted_path = convert_ogg_opus_to_mp3(tmp_path)
            if converted_path == tmp_path:
                logger.warning("[AUDIO] ⚠️ Konversi gagal, akan coba kirim file OGG langsung ke AudD")
            else:
                logger.info(f"[AUDIO] Konversi selesai → {converted_path}")
        else:
            converted_path = tmp_path
            logger.info(f"[AUDIO] Format bukan OGG ({ext}), langsung kirim ke AudD")

        logger.info(f"[AUDIO] Mengirim ke AudD: {converted_path}")
        result = recognize_with_audd(file_path=converted_path)

        if result is False:
            logger.error("[AUDIO] AudD return False — semua key habis/error")
            await loading_msg.edit_text("⏳ Please try again in a moment!")
            return
        if result is None:
            logger.info("[AUDIO] AudD return None — lagu tidak dikenali")
            await loading_msg.edit_text("❌ Song not recognized. Try a longer or clearer clip!")
            return

        logger.info(f"[AUDIO] ✅ Sukses! Mengirim hasil ke user...")
        await loading_msg.delete()
        await send_result_telegram(message, build_result_payload(result))

    except Exception as e:
        logger.error(f"[AUDIO] ❌ Unexpected error: {e}\n{traceback.format_exc()}")
        await loading_msg.edit_text("⏳ Please try again in a moment!")
    finally:
        for path in set(filter(None, [tmp_path, converted_path])):
            if os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.debug(f"[AUDIO] Cleanup: hapus {path}")
                except Exception as ce:
                    logger.warning(f"[AUDIO] Gagal hapus temp file {path}: {ce}")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    logger.info(f"[URL] Pesan masuk dari user={user.id} (@{user.username})")

    if is_group_or_channel(update) and not is_mentioned(update, BOT_USERNAME):
        logger.debug("[URL] Di grup/channel tapi tidak di-mention, skip.")
        return

    raw_text = (message.text or message.caption or "").strip()
    text = strip_mention(raw_text, BOT_USERNAME)
    url  = is_valid_url(text)

    if not url:
        logger.debug(f"[URL] Tidak ada URL valid di teks: '{text[:100]}'")
        return

    logger.info(f"[URL] URL ditemukan: {url}")

    if is_unsupported_url(url):
        logger.info(f"[URL] URL tidak didukung (YouTube/Twitch): {url}")
        await message.reply_text("⚠️ YouTube dan Twitch tidak support.\n\nKirim link langsung ke file audio/video atau pakai voice note!")
        return

    loading_msg = await message.reply_text("🎧 Listening... hang on!")
    try:
        result = recognize_with_audd(url=url)
        if result is False:
            logger.error("[URL] AudD return False — semua key habis/error")
            await loading_msg.edit_text("⏳ Please try again in a moment!")
            return
        if result is None:
            logger.info("[URL] AudD return None — lagu tidak dikenali dari URL")
            await loading_msg.edit_text("❌ Song not recognized. The URL may not contain music.")
            return
        logger.info("[URL] ✅ Sukses! Mengirim hasil ke user...")
        await loading_msg.delete()
        await send_result_telegram(message, build_result_payload(result))
    except Exception as e:
        logger.error(f"[URL] ❌ Unexpected error: {e}\n{traceback.format_exc()}")
        await loading_msg.edit_text("⏳ Please try again in a moment!")


def main():
    global BOT_USERNAME
    import asyncio

    logger.info("=" * 50)
    logger.info("[STARTUP] WhatSongLyricsIsThis Bot starting...")
    logger.info(f"[STARTUP] AUDD keys terdaftar: {len(AUDD_API_KEYS)}")
    check_ffmpeg()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    async def _fetch_username():
        global BOT_USERNAME
        bot_info = await app.bot.get_me()
        BOT_USERNAME = bot_info.username
        logger.info(f"[STARTUP] Bot username: @{BOT_USERNAME}")
        logger.info(f"[STARTUP] Bot name: {bot_info.first_name}")
        logger.info(f"[STARTUP] Bot ID: {bot_info.id}")

    asyncio.get_event_loop().run_until_complete(_fetch_username())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_url))

    logger.info("[STARTUP] ✅ Bot siap menerima pesan!")
    logger.info("=" * 50)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
