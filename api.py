import os
import re
import logging
import requests
import tempfile
import subprocess
import base64 as b64mod
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

API_PORT = int(os.environ.get("PORT", os.environ.get("API_PORT", 5000)))
AUDD_API_KEYS = [
    os.environ.get("AUDD_API_KEY_1", ""),
    os.environ.get("AUDD_API_KEY_2", ""),
    os.environ.get("AUDD_API_KEY_3", ""),
]
AUDD_API_KEYS = [k for k in AUDD_API_KEYS if k]

# Hardcoded API key untuk testing
VALID_API_KEYS = {"wuhavers-test-key-2024"}

UNSUPPORTED_URLS = [
    r"(youtube\.com|youtu\.be)",
    r"twitch\.tv",
]

ALLOWED_ORIGINS = [
    "https://wuhavers.biz.id",
    "https://www.wuhavers.biz.id",
    "https://wuhavers.com",
    "https://www.wuhavers.com",
    "https://wuhavers.pages.dev",
    "https://whatissong.onrender.com",
]

app = Flask(__name__)
CORS(app, origins=ALLOWED_ORIGINS, allow_headers=["Content-Type", "X-API-Key"], methods=["GET", "POST", "OPTIONS"])


def check_api_key():
    """Cek API key dari header X-API-Key atau query param api_key."""
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if not key or key not in VALID_API_KEYS:
        return False
    return True


def check_origin():
    """Cek apakah request dari domain yang diizinkan."""
    origin = request.headers.get("Origin") or request.headers.get("Referer", "")
    allowed = any(origin.startswith(o) for o in ALLOWED_ORIGINS)
    return allowed


def is_unsupported_url(url):
    for pattern in UNSUPPORTED_URLS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def convert_ogg_opus_to_mp3(input_path):
    output_path = input_path + ".mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-acodec", "libopus", "-i", input_path,
             "-ar", "48000", "-ac", "1", "-b:a", "128k", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return output_path
    except:
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-ar", "48000", "-ac", "1", "-b:a", "128k", output_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            return output_path
        except:
            return input_path


def recognize_with_audd(file_path=None, url=None):
    if not AUDD_API_KEYS:
        return False
    for i, api_key in enumerate(AUDD_API_KEYS):
        try:
            data = {"api_token": api_key, "return": "spotify,apple_music"}
            if url:
                data["url"] = url
                response = requests.post("https://api.audd.io/", data=data, timeout=30)
            else:
                with open(file_path, "rb") as f:
                    response = requests.post("https://api.audd.io/", data=data, files={"file": f}, timeout=30)
            result_data = response.json()
            if result_data.get("status") == "success":
                return result_data.get("result")
            error = result_data.get("error", {})
            error_code = error.get("error_code") if isinstance(error, dict) else None
            if error_code in (900, 901):
                continue
            return None
        except:
            continue
    return False


def get_spotify_url(result):
    try:
        return result["spotify"]["external_urls"]["spotify"]
    except:
        return None


def get_cover_url(result):
    try:
        url = result["apple_music"]["artwork"]["url"]
        return url.replace("{w}", "500").replace("{h}", "500")
    except:
        return None


def get_youtube_search(artist, title):
    query = requests.utils.quote(f"{artist} {title}")
    return f"https://www.youtube.com/results?search_query={query}"


def get_lyrics(artist, title):
    try:
        url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(title)}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            lyrics = resp.json().get("lyrics", "").strip()
            if lyrics:
                return lyrics[:3000] + ("\n\n... (truncated)" if len(lyrics) > 3000 else "")
    except:
        pass
    return None


def get_deezer_preview(artist, title):
    try:
        query = requests.utils.quote(f"{artist} {title}")
        resp = requests.get(f"https://api.deezer.com/search?q={query}&limit=1", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tracks = data.get("data", [])
            if tracks:
                return tracks[0].get("preview")
    except:
        pass
    return None


def build_result_payload(result):
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


@app.route("/recognize", methods=["POST"])
def api_recognize():
    # Cek origin
    if not check_origin():
        return jsonify({"error": "Origin tidak diizinkan."}), 403

    # Cek API key
    if not check_api_key():
        return jsonify({"error": "API key tidak valid atau tidak ditemukan."}), 401

    tmp_path = converted_path = None
    try:
        if "file" in request.files:
            f = request.files["file"]
            ext = os.path.splitext(f.filename or "audio.mp3")[1] or ".mp3"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp_path = tmp.name
            f.save(tmp_path)
            if ext.lower() in (".ogg", ".webm", ".opus"):
                converted_path = convert_ogg_opus_to_mp3(tmp_path)
            else:
                converted_path = tmp_path
            result = recognize_with_audd(file_path=converted_path)

        elif request.form.get("audio_b64"):
            audio_data = b64mod.b64decode(request.form["audio_b64"])
            filename = request.form.get("filename", "audio.webm")
            ext = os.path.splitext(filename)[1] or ".webm"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(audio_data)
            if ext.lower() in (".ogg", ".webm", ".opus"):
                converted_path = convert_ogg_opus_to_mp3(tmp_path)
            else:
                converted_path = tmp_path
            result = recognize_with_audd(file_path=converted_path)

        elif request.is_json and request.json.get("url"):
            url = request.json["url"]
            if is_unsupported_url(url):
                return jsonify({"error": "YouTube dan Twitch tidak didukung."}), 400
            result = recognize_with_audd(url=url)

        else:
            return jsonify({"error": "Kirim file audio atau JSON {\"url\":\"...\"}"}), 400

        if result is False:
            return jsonify({"error": "Semua API key AudD habis kuota."}), 503
        if result is None:
            return jsonify({"error": "Lagu tidak dikenali."}), 404

        return jsonify(build_result_payload(result))

    except Exception as e:
        import traceback
        logger.error(f"API error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": "Internal server error."}), 500

    finally:
        for path in set(filter(None, [tmp_path, converted_path])):
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except:
                    pass


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "whatissong-api",
        "keys_loaded": len(AUDD_API_KEYS),
    })


PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")

@app.route("/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(PUBLIC_DIR, filename)

if __name__ == "__main__":
    logger.info(f"Flask API listening on port {API_PORT}")
    app.run(host="0.0.0.0", port=API_PORT, debug=False)
