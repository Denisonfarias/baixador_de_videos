"""Baixador meutudo — versão cloud (Render/Docker)."""
import os
import re
import secrets
import shutil
import tempfile
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    after_this_request,
    jsonify,
    make_response,
    render_template,
    request,
    send_file,
)

import yt_dlp

app = Flask(__name__)

YOUTUBE_RE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
COOKIES_FILE = os.environ.get("COOKIES_FILE", "")

FORMATS = {
    "mp4_best": {"format": "bv*+ba/b", "merge_output_format": "mp4", "ext": "mp4"},
    "mp4_1080": {
        "format": "bv*[height<=1080]+ba/b[height<=1080]",
        "merge_output_format": "mp4",
        "ext": "mp4",
    },
    "mp4_720": {
        "format": "bv*[height<=720]+ba/b[height<=720]",
        "merge_output_format": "mp4",
        "ext": "mp4",
    },
    "mp3": {
        "format": "ba/b",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
        "ext": "mp3",
    },
}


def auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not APP_PASSWORD:
            resp = make_response(
                "Servidor sem APP_PASSWORD configurada. Defina a variável no painel do Render.",
                500,
            )
            return resp
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.password or "", APP_PASSWORD):
            resp = make_response("Acesso restrito.", 401)
            resp.headers["WWW-Authenticate"] = 'Basic realm="Baixador meutudo"'
            return resp
        return view(*args, **kwargs)

    return wrapped


def build_yt_opts(fmt: str, outtmpl: str) -> dict:
    fmt_cfg = {k: v for k, v in FORMATS[fmt].items() if k != "ext"}
    opts = {
        **fmt_cfg,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
    }
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE
    return opts


def yt_error(prefix: str, exc: Exception):
    msg = str(exc)
    if "Sign in to confirm" in msg or "confirm you're not a bot" in msg.lower():
        msg = (
            "YouTube bloqueou (anti-bot). Para resolver, configure a variável "
            "COOKIES_FILE com cookies de uma conta logada."
        )
    return jsonify({"error": f"{prefix}: {msg}"}), 500


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/")
@auth_required
def index():
    return render_template("index.html")


@app.route("/api/download", methods=["POST"])
@auth_required
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = data.get("format") or "mp4_best"

    if not YOUTUBE_RE.match(url):
        return jsonify({"error": "URL inválida. Use um link do YouTube."}), 400
    if fmt not in FORMATS:
        return jsonify({"error": "Formato não suportado."}), 400

    final_ext = FORMATS[fmt]["ext"]
    tmp_root = Path(tempfile.mkdtemp(prefix="baixador-"))

    opts = build_yt_opts(fmt, str(tmp_root / "%(title).150B.%(ext)s"))

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return yt_error("Falha ao baixar", exc)

    files = [p for p in tmp_root.rglob("*") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        shutil.rmtree(tmp_root, ignore_errors=True)
        return jsonify({"error": "Arquivo não encontrado após download."}), 500

    final = files[0]

    @after_this_request
    def cleanup(response):
        shutil.rmtree(tmp_root, ignore_errors=True)
        return response

    download_name = final.name
    response = send_file(
        final,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/octet-stream",
    )
    response.headers["X-Filename"] = download_name
    response.headers["Access-Control-Expose-Headers"] = "X-Filename, Content-Disposition"
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
