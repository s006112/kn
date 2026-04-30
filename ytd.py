#!/usr/bin/env python3
"""Minimal standalone web UI for yt-dlp."""

import argparse
import errno
import html
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

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
X_FORMAT_ARGS = [
    "-f",
    "http-2176/bestvideo+bestaudio/best",
    "--merge-output-format",
    "mp4",
]
X_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
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
</main>
"""


def detect_js_runtime():
    if runtime := os.getenv("YTD_JS_RUNTIME", "").strip():
        return runtime
    for name in ("deno", "node", "bun", "qjs", "quickjs"):
        if path := shutil.which(name):
            return "quickjs" if name == "quickjs" else f"quickjs:{path}" if name == "qjs" else name
    return ""


def build_common_args(include_cookie_sources=True):
    args = [
        item
        for flag, value in (
            ("--js-runtimes", detect_js_runtime()),
            ("--remote-components", os.getenv("YTD_REMOTE_COMPONENTS", "ejs:github").strip()),
            ("--extractor-args", os.getenv("YTD_EXTRACTOR_ARGS", "youtube:player_client=default").strip()),
            ("--cookies", os.getenv("YTD_COOKIES_FILE", "").strip()) if include_cookie_sources else ("", ""),
            (
                "--cookies-from-browser",
                os.getenv("YTD_COOKIES_FROM_BROWSER", "").strip(),
            )
            if include_cookie_sources
            else ("", ""),
        )
        if flag and value
        for item in (flag, value)
    ]
    return args + shlex.split(os.getenv("YTD_EXTRA_ARGS", ""))


COMMON_ARGS = build_common_args()
X_COMMON_ARGS = build_common_args(include_cookie_sources=False)


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


def is_x_twitter_url(url):
    host = urlsplit(url).netloc.lower().split(":", 1)[0]
    return host == "x.com" or host.endswith(".x.com") or host == "twitter.com" or host.endswith(".twitter.com")


def get_x_auth():
    auth_token = os.getenv("X_AUTH_TOKEN", "").strip()
    ct0 = os.getenv("X_CT0", "").strip()
    if not auth_token or not ct0:
        raise RuntimeError("缺少 X/Twitter 下载所需环境变量：X_AUTH_TOKEN 和 X_CT0。请先在环境变量或 .env 中设置。")
    return auth_token, ct0


def resolve_x_url(url, auth_token, ct0):
    print(f"Resolving X/Twitter URL: {url}")
    request = Request(
        url,
        headers={
            "User-Agent": X_USER_AGENT,
            "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            resolved_url = response.geturl()
    except OSError as exc:
        raise RuntimeError("解析 X/Twitter 链接失败，请确认链接可访问且当前认证信息有效。") from exc
    resolved_url = clean_url(resolved_url) or resolved_url
    print(f"Resolved X/Twitter URL: {resolved_url}")
    return resolved_url


def create_x_cookie_file(temp_dir, auth_token, ct0):
    cookie_content = (
        "# Netscape HTTP Cookie File\n"
        f".x.com\tTRUE\t/\tTRUE\t2000000000\tauth_token\t{auth_token}\n"
        f".x.com\tTRUE\t/\tTRUE\t2000000000\tct0\t{ct0}\n"
        f".twitter.com\tTRUE\t/\tTRUE\t2000000000\tauth_token\t{auth_token}\n"
        f".twitter.com\tTRUE\t/\tTRUE\t2000000000\tct0\t{ct0}\n"
    )
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            prefix="x_cookies_",
            suffix=".txt",
            encoding="utf-8",
        ) as temp_cookie:
            temp_cookie.write(cookie_content)
            return temp_cookie.name
    except OSError as exc:
        raise RuntimeError("创建 X/Twitter 临时 cookie 文件失败。") from exc


def run_yt_dlp(cmd, temp_dir):
    print("Running:", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("系统里找不到 yt-dlp。") from exc
    lines = []
    shown = -1
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.strip()
        if line:
            lines.append(line)
        if "[download]" not in line:
            continue
        match = re.search(r"(\d+(?:\.\d+)?)%", line)
        if not match:
            continue
        pct = max(0, min(100, int(float(match.group(1)))))
        if pct == shown:
            continue
        shown = pct
        width = 40
        filled = width * pct // 100
        print(f"\r[{'█' * filled}{'-' * (width - filled)}] {pct:3d}%", end="", flush=True)
    proc.stdout.close()
    return_code = proc.wait()
    if shown >= 0:
        print()
    if return_code:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("yt-dlp failed:\n" + ("\n".join(lines[-10:]) or "Unknown yt-dlp error."))
    if not (files := [path for path in Path(temp_dir).iterdir() if path.is_file()]):
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("下载完成，但没有生成文件。")
    return max(files, key=lambda path: path.stat().st_mtime), temp_dir


def download_youtube(url, mode):
    if mode not in FORMATS:
        raise RuntimeError("无效下载模式。")
    temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    cmd = ["yt-dlp", "--newline", *FORMATS[mode], "-o", "%(title).50s.%(ext)s", *COMMON_ARGS, url]
    print(f"Download request: {url} ({mode})")
    return run_yt_dlp(cmd, temp_dir)


def download_x_twitter(url):
    temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    try:
        auth_token, ct0 = get_x_auth()
        resolved_url = resolve_x_url(url, auth_token, ct0)
        cookie_path = create_x_cookie_file(temp_dir, auth_token, ct0)
        cmd = [
            "yt-dlp",
            "--newline",
            *X_FORMAT_ARGS,
            "-o",
            "%(uploader)s_%(id)s.%(ext)s",
            *X_COMMON_ARGS,
            "--cookies",
            cookie_path,
            resolved_url,
        ]
        print(f"Download request: {resolved_url} (x/twitter)")
        try:
            return run_yt_dlp(cmd, temp_dir)
        finally:
            try:
                os.remove(cookie_path)
            except FileNotFoundError:
                pass
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def download(url, mode):
    if is_x_twitter_url(url):
        return download_x_twitter(url)
    return download_youtube(url, mode)


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
        path, temp_dir = download(url, mode)
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
