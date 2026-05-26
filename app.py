"""Baixador meutudo — app local Flask para download de vídeos do YouTube via yt-dlp."""
import os
import re
import shutil
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

import yt_dlp

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)


def find_ffmpeg() -> str | None:
    if shutil.which("ffmpeg"):
        return None  # yt-dlp will find it via PATH
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links",
    ]
    for root in candidates:
        if not root.exists():
            continue
        for exe in root.rglob("ffmpeg.exe"):
            return str(exe.parent)
    return None


FFMPEG_DIR = find_ffmpeg()

app = Flask(__name__)

YOUTUBE_RE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/")
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")

FORMATS = {
    "mp4_best": {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
    },
    "mp4_1080": {
        "format": "bv*[height<=1080]+ba/b[height<=1080]",
        "merge_output_format": "mp4",
    },
    "mp4_720": {
        "format": "bv*[height<=720]+ba/b[height<=720]",
        "merge_output_format": "mp4",
    },
    "mp3": {
        "format": "ba/b",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ],
    },
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not YOUTUBE_RE.match(url):
        return jsonify({"error": "URL inválida. Use um link do YouTube."}), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            meta = ydl.extract_info(url, download=False)
        return jsonify(
            {
                "title": meta.get("title"),
                "uploader": meta.get("uploader"),
                "duration": meta.get("duration"),
                "thumbnail": meta.get("thumbnail"),
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Falha ao ler vídeo: {exc}"}), 500


@app.route("/api/download", methods=["POST"])
def download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = data.get("format") or "mp4_best"

    if not YOUTUBE_RE.match(url):
        return jsonify({"error": "URL inválida. Use um link do YouTube."}), 400
    if fmt not in FORMATS:
        return jsonify({"error": "Formato não suportado."}), 400

    job_id = uuid.uuid4().hex[:12]
    out_dir = DOWNLOADS_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        **FORMATS[fmt],
        "outtmpl": str(out_dir / "%(title).180B.%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
    }
    if FFMPEG_DIR:
        opts["ffmpeg_location"] = FFMPEG_DIR

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=True)
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        return jsonify({"error": f"Falha ao baixar: {exc}"}), 500

    files = sorted(out_dir.iterdir(), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        shutil.rmtree(out_dir, ignore_errors=True)
        return jsonify({"error": "Nenhum arquivo gerado."}), 500

    final = files[0]
    return jsonify(
        {
            "title": meta.get("title", "video"),
            "filename": final.name,
            "size_mb": round(final.stat().st_size / (1024 * 1024), 2),
            "download_url": f"/files/{job_id}/{final.name}",
        }
    )


@app.route("/files/<job_id>/<path:filename>")
def serve_file(job_id, filename):
    if not JOB_ID_RE.match(job_id):
        abort(400)
    return send_from_directory(DOWNLOADS_DIR / job_id, filename, as_attachment=True)


if __name__ == "__main__":
    print("Baixador meutudo rodando em http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
