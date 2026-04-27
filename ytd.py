#!/usr/bin/env python3
"""Minimal standalone web UI for yt-dlp."""

import argparse
import errno
import html
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

MAX_POST_BYTES = 4096
TRACKING_KEYS = {"fbclid", "feature", "pp", "si", "t"}
FORMATS = {
    "worst": ["-f", "(worstvideo[ext=mp4]+worstaudio[ext=m4a])/(worstvideo+worstaudio)/worst"],
    "720p": [
        "-f",
        "(bestvideo[ext=mp4][height=720]+bestaudio[ext=m4a])/"
        "(bestvideo[height=720]+bestaudio)/"
        "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a])/"
        "(bestvideo[height<=720]+bestaudio)/best[height<=720]",
        "--merge-output-format",
        "mp4",
    ],
    "mp3": ["-x", "--audio-format", "mp3", "-f", "bestaudio/best"],
}
FORM_HTML = """<!doctype html>
<meta charset="utf-8">
<title>yt-dlp downloader</title>
<style>
body{font-family:sans-serif;margin:2rem;background:#f6f6f6}
main{max-width:560px;margin:auto;background:#fff;padding:1.5rem;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,.1)}
form{display:flex;flex-wrap:wrap;gap:.5rem}
input{flex:1 1 100%;padding:.7rem;border:1px solid #ccc;border-radius:4px}
button{padding:.65rem 1rem;border:0;border-radius:4px;background:#0067c0;color:#fff;cursor:pointer}
.status{margin-top:1rem;padding:.75rem;border-radius:4px}.info{background:#eef7ff;color:#0c3a62}.error{background:#fdecea;color:#611a15}
small{display:block;margin-top:1rem;color:#555}
</style>
<main>
  <h2>yt-dlp downloader</h2>
  <form method="post">
    <input name="url" placeholder="https://youtube.com/watch?v=..." required>
    <button name="mode" value="worst">最低</button>
    <button name="mode" value="720p">720p</button>
    <button name="mode" value="mp3">MP3</button>
  </form>
  {status}
  <small>只保留最低画质、720p 和 MP3。文件在发送后会删除。</small>
</main>
"""


def detect_js_runtime():
    if runtime := os.getenv("YTD_JS_RUNTIME", "").strip():
        return runtime
    for label, binary in (
        ("deno", "deno"),
        ("node", "node"),
        ("bun", "bun"),
        ("quickjs", "qjs"),
        ("quickjs", "quickjs"),
    ):
        if path := shutil.which(binary):
            return label if label == binary else f"{label}:{path}"
    return ""


def common_args():
    values = [
        ("--js-runtimes", detect_js_runtime()),
        ("--remote-components", os.getenv("YTD_REMOTE_COMPONENTS", "ejs:github").strip()),
        ("--extractor-args", os.getenv("YTD_EXTRACTOR_ARGS", "youtube:player_client=default").strip()),
        ("--cookies", os.getenv("YTD_COOKIES_FILE", "").strip()),
        ("--cookies-from-browser", os.getenv("YTD_COOKIES_FROM_BROWSER", "").strip()),
    ]
    args = [item for flag, value in values if value for item in (flag, value)]
    return args + shlex.split(os.getenv("YTD_EXTRA_ARGS", ""))


COMMON_ARGS = common_args()


def clean_url(url):
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url.lstrip("/")
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if "youtu.be" in host:
        return urlunsplit((parts.scheme or "https", parts.netloc, parts.path.rstrip("/"), "", ""))
    if "youtube.com" in host or "youtube-nocookie.com" in host:
        if parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0].strip()
            return f"{parts.scheme or 'https'}://{parts.netloc}/watch?v={video_id}" if video_id else ""
        return urlunsplit((parts.scheme or "https", parts.netloc, parts.path.rstrip("/"), "", ""))
    query = [
        (key, value)
        for key, values in parse_qs(parts.query, keep_blank_values=True).items()
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
        for value in values
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))


class DownloadHandler(BaseHTTPRequestHandler):
    server_version = "MinimalYTD/0.2"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.render("Ready.")

    def do_POST(self):
        try:
            url, mode = self.read_form()
            self.log_message("Download request: %s (%s)", url, mode)
            with tempfile.TemporaryDirectory(prefix="ytdlp_") as temp_dir:
                self.send_file(self.download(url, mode, temp_dir))
        except RuntimeError as exc:
            self.render(str(exc), True)

    def read_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RuntimeError("Content-Length 无效。") from exc
        if length <= 0:
            raise RuntimeError("缺少表单数据。")
        if length > MAX_POST_BYTES:
            self.rfile.read(length)
            raise RuntimeError("表单过大。")
        params = parse_qs(self.rfile.read(length).decode("utf-8", "ignore"), keep_blank_values=True)
        url = clean_url(params.get("url", [""])[0])
        if not url:
            raise RuntimeError("请输入有效链接。")
        mode = params.get("mode", ["worst"])[0].strip().lower()
        return url, mode if mode in FORMATS else "worst"

    def download(self, url, mode, temp_dir):
        cmd = ["yt-dlp", *FORMATS[mode], "-o", "%(title).50s.%(ext)s", *COMMON_ARGS, url]
        self.log_message("Running: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("系统里找不到 yt-dlp。") from exc
        if proc.returncode:
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            raise RuntimeError("yt-dlp failed:\n" + ("\n".join(lines[-10:]) or "Unknown yt-dlp error."))
        files = [path for path in Path(temp_dir).iterdir() if path.is_file()]
        if not files:
            raise RuntimeError("下载完成，但没有生成文件。")
        return max(files, key=lambda path: path.stat().st_mtime)

    def send_file(self, path):
        size = path.stat().st_size
        name = path.name
        fallback = name.encode("ascii", "ignore").decode("ascii").replace('"', "_") or "download.bin"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name)}')
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with path.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 64 * 1024)
        except BrokenPipeError:
            self.log_message("Client canceled download: %s", name)

    def render(self, message, is_error=False):
        status = f'<p class="status {"error" if is_error else "info"}">{html.escape(message)}</p>' if message else ""
        body = FORM_HTML.replace("{status}", status).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def parse_args():
    parser = argparse.ArgumentParser(description="Minimal yt-dlp download helper.")
    parser.add_argument("--host", default=os.getenv("YTD_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("YTD_PORT", "8765")))
    return parser.parse_args()


def main():
    args = parse_args()

    class Server(ThreadingHTTPServer):
        allow_reuse_address = True

    try:
        server = Server((args.host, args.port), DownloadHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                "启动失败：端口已被占用。\n"
                f"- 当前地址：{args.host}:{args.port}\n"
                "- 可改用：`python3 ytd.py --port 8766` 或 `python3 ytd.py --port 0`"
            )
            raise SystemExit(2) from exc
        raise

    host, port = server.server_address[:2]
    print(f"Serving downloader on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
