#!/usr/bin/env python3
"""Minimal standalone web UI for yt-dlp."""

import argparse
import errno
import html
import os
import shutil
from urllib.parse import parse_qs, quote
from wsgiref.simple_server import make_server

from helper.helper_ytd import clean_url, download, download_ttml_or_video

MAX_POST_BYTES = 4096
FORM_HTML = """<!doctype html>
<meta charset="utf-8">
<title>yt downloader</title>
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
  <h2>yt downloader</h2>
  <form method="post">
    <input name="url" placeholder="https://youtube.com/watch?v=..." required>
    <button name="mode" value="worst">最低</button>
    <button name="mode" value="720p">720p</button>
    <button name="mode" value="mp3">MP3</button>
  </form>
  {status}
</main>
"""


def app(environ, start_response):
    def reply(message, is_error=False):
        status = f'<p class="status {"error" if is_error else "info"}">{html.escape(message)}</p>' if message else ""
        body = FORM_HTML.replace("{status}", status).encode()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))])
        return [body]

    if environ.get("REQUEST_METHOD") != "POST":
        return reply("Ready.")
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
        if not 0 < length <= MAX_POST_BYTES:
            raise RuntimeError("缺少表单数据。" if length <= 0 else "表单过大。")
        params = parse_qs(environ["wsgi.input"].read(length).decode("utf-8", "ignore"), keep_blank_values=True)
        if not (url := clean_url(params.get("url", [""])[0])):
            raise RuntimeError("请输入有效链接。")
        mode = params.get("mode", ["worst"])[0].strip().lower()
        if mode == "worst":
            path, temp_dir = download_ttml_or_video(url, "worst")
        else:
            path, temp_dir = download(url, mode)
        if not path.is_file():
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError("下载完成，但生成的文件已丢失，请重试。")
    except ValueError:
        return reply("Content-Length 无效。", True)
    except RuntimeError as exc:
        return reply(str(exc), True)
    name = path.name
    fallback = name.encode("ascii", "ignore").decode("ascii").replace('"', "_") or "download.bin"
    start_response(
        "200 OK",
        [
            ("Content-Type", "application/octet-stream"),
            ("Content-Disposition", f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(name)}'),
            ("Content-Length", str(path.stat().st_size)),
            ("Cache-Control", "no-store"),
        ],
    )

    def stream():
        try:
            with path.open("rb") as fh:
                while chunk := fh.read(64 * 1024):
                    yield chunk
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    return stream()


def main():
    parser = argparse.ArgumentParser(description="Minimal yt-dlp download helper.")
    parser.add_argument("--host", default=os.getenv("YTD_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("YTD_PORT", "8765")))
    args = parser.parse_args()
    try:
        server = make_server(args.host, args.port, app)
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
