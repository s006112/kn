#!/usr/bin/env python3
"""
Minimal standalone web UI for downloading audio via yt-dlp.
"""

import argparse
import html
import os
import errno
import shlex
import shutil
import subprocess
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote

try:
    from http.server import ThreadingHTTPServer
except ImportError:  # pragma: no cover (fallback for very old Python)
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True


MAX_POST_BYTES = 4096
FORM_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Downloader</title>
  <style>
    body {{font-family: sans-serif; margin: 2rem; background: #f6f6f6;}}
    main {{max-width: 480px; margin: auto; background: #fff; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);}}
    form {{display: flex; gap: 0.5rem;}}
    form + form {{margin-top: 0.5rem;}}
    input[type=text] {{flex: 1; padding: 0.6rem; border: 1px solid #ccc; border-radius: 4px;}}
    button {{padding: 0.6rem 1rem; border: none; border-radius: 4px; background: #0067c0; color: #fff; cursor: pointer;}}
    button:hover {{background: #00509d;}}
    p.status {{margin-top: 1rem; padding: 0.75rem; border-radius: 4px;}}
    p.info {{background: #eef7ff; color: #0c3a62;}}
    p.error {{background: #fdecea; color: #611a15;}}
    small {{color: #555;}}
  </style>
</head>
<body>
  <main>
    <h2>Download audio</h2>
    <form method="post">
      <input type="hidden" name="mode" value="worst">
      <input type="text" name="url" placeholder="https://example.com/video" required>
      <button type="submit">Fetch</button>
    </form>
    <form method="post">
      <input type="hidden" name="mode" value="720p">
      <input type="text" name="url" placeholder="https://example.com/video (720p)" required>
      <button type="submit">720p</button>
    </form>
    <form method="post">
      <input type="hidden" name="mode" value="mp3">
      <input type="text" name="url" placeholder="https://youtube.com/watch?v=... (MP3)" required>
      <button type="submit">MP3</button>
    </form>
    {status}
    <small>Top form runs. Files are removed after each request.</small>
  </main>
</body>
</html>
"""


def _limit_filename_length(filename, limit=50):
    if len(filename) <= limit:
        return filename
    name, ext = os.path.splitext(filename)
    max_name_len = max(1, limit - len(ext))
    trimmed = name[:max_name_len]
    return trimmed + ext


def _split_env_args(var_name):
    value = os.environ.get(var_name)
    return shlex.split(value) if value else []


def _detect_js_runtime():
    configured = os.environ.get("YTD_JS_RUNTIME")
    if configured:
        trimmed = configured.strip()
        return trimmed or None
    candidates = [
        ("deno", "deno"),
        ("node", "node"),
        ("bun", "bun"),
        ("quickjs", "qjs"),
        ("quickjs", "quickjs"),
    ]
    for runtime, binary in candidates:
        path = shutil.which(binary)
        if path:
            return runtime if runtime == binary else f"{runtime}:{path}"
    return None


JS_RUNTIME = _detect_js_runtime()
REMOTE_COMPONENTS = os.environ.get("YTD_REMOTE_COMPONENTS", "ejs:github").strip() or None
EXTRACTOR_ARGS = os.environ.get("YTD_EXTRACTOR_ARGS", "youtube:player_client=default").strip() or None
COOKIES_FILE = os.environ.get("YTD_COOKIES_FILE")
COOKIES_FROM_BROWSER = os.environ.get("YTD_COOKIES_FROM_BROWSER")
EXTRA_ARGS = _split_env_args("YTD_EXTRA_ARGS")


class DownloadHandler(BaseHTTPRequestHandler):
    server_version = "MinimalYTD/0.1"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._render_form("Ready.")

    def do_POST(self):
        url, mode, error = self._extract_request()
        if error:
            self._render_form(error, is_error=True)
            return

        temp_dir = None
        try:
            self.log_message("Received download request for %s", url)
            temp_dir, file_path = self._download_with_yt_dlp(url, mode)
            self._send_file(file_path)
            self.log_message("Completed download request for %s", url)
        except RuntimeError as exc:
            self.log_message("Request failed for %s: %s", url, exc)
            self._render_form(str(exc), is_error=True)
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

    # Helper methods -----------------------------------------------------

    def _extract_request(self):
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return None, None, "Missing Content-Length header."
        try:
            length = int(length_header)
        except ValueError:
            return None, None, "Invalid Content-Length value."

        if length <= 0:
            return None, None, "Missing form data."

        if length > MAX_POST_BYTES:
            self.rfile.read(length)  # drain oversize body
            return None, None, "Form data is too large."

        raw = self.rfile.read(length)
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded = raw.decode("utf-8", "ignore")

        params = parse_qs(decoded, keep_blank_values=True)
        url = params.get("url", [""])[0].strip()
        if not url:
            return None, None, "Please enter a URL."
        mode = params.get("mode", ["worst"])[0].strip().lower()
        if mode not in {"worst", "720p", "mp3"}:
            mode = "worst"
        return url, mode, None

    def _yt_dlp_common_args(self):
        cmd = []
        if JS_RUNTIME:
            cmd += ["--js-runtimes", JS_RUNTIME]
        if REMOTE_COMPONENTS:
            cmd += ["--remote-components", REMOTE_COMPONENTS]
        if EXTRACTOR_ARGS:
            cmd += ["--extractor-args", EXTRACTOR_ARGS]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]
        if COOKIES_FROM_BROWSER:
            cmd += ["--cookies-from-browser", COOKIES_FROM_BROWSER]
        if EXTRA_ARGS:
            cmd += EXTRA_ARGS
        return cmd

    def _download_with_yt_dlp(self, url, mode):
        temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
        if mode == "mp3":
            cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-f", "bestaudio/best"]
        else:
            is_720p = mode == "720p"
            # Keep the Fetch action intentionally small by forcing the worst
            # available muxed video+audio combination. The 720p action attempts an
            # exact 720p grab first before falling back to any format at or below
            # 720p so that users get the intended resolution when available.
            format_selector = (
                "(bestvideo[ext=mp4][height=720]+bestaudio[ext=m4a])/"
                "(bestvideo[height=720]+bestaudio)/"
                "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a])/"
                "(bestvideo[height<=720]+bestaudio)/"
                "best[height<=720]"
                if is_720p
                else "(worstvideo[ext=mp4]+worstaudio[ext=m4a])/(worstvideo+worstaudio)/worst"
            )
            cmd = ["yt-dlp", "-f", format_selector]
            if is_720p:
                cmd += ["--merge-output-format", "mp4"]

        cmd += ["-o", "%(title).50s.%(ext)s"]
        cmd += self._yt_dlp_common_args()
        cmd.append(url)
        self.log_message("Starting yt-dlp process: %s", " ".join(cmd))

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
            raise RuntimeError("yt-dlp executable is not available on this system.") from exc

        output_lines = []
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            output_lines.append(line)
            if line:
                self.log_message("[yt-dlp] %s", line)
        proc.stdout.close()
        return_code = proc.wait()

        if return_code != 0:
            snippet = "\n".join(line for line in output_lines[-10:] if line.strip()) or "Unknown yt-dlp error."
            raise RuntimeError(f"yt-dlp failed:\n{snippet}")

        self.log_message("yt-dlp finished successfully with %d lines of output.", len(output_lines))

        files = [
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if os.path.isfile(os.path.join(temp_dir, name))
        ]
        if not files:
            raise RuntimeError("Download completed but no file was created.")

        latest = max(files, key=os.path.getmtime)
        limited_name = _limit_filename_length(os.path.basename(latest))
        if limited_name != os.path.basename(latest):
            new_path = os.path.join(temp_dir, limited_name)
            os.replace(latest, new_path)
            latest = new_path
        return temp_dir, latest

    def _send_file(self, file_path):
        filename = os.path.basename(file_path)
        try:
            size = os.path.getsize(file_path)
        except OSError as exc:
            raise RuntimeError(f"Downloaded file missing: {exc}") from exc

        ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "download.bin"
        ascii_fallback = ascii_fallback.replace('"', "_")
        utf8_name = quote(filename)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_name}'
        self.send_header("Content-Disposition", disposition)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            with open(file_path, "rb") as src:
                shutil.copyfileobj(src, self.wfile, length=64 * 1024)
        except BrokenPipeError:
            self.log_message("Client canceled download for %s", filename)

    def _render_form(self, message, is_error=False):
        css_class = "error" if is_error else "info"
        status_block = f'<p class="status {css_class}">{html.escape(message)}</p>' if message else ""
        body = FORM_HTML.format(status=status_block)
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    # Reduce default noisy logging
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def parse_args():
    parser = argparse.ArgumentParser(description="Minimal yt-dlp download helper.")
    parser.add_argument(
        "--host",
        default=os.environ.get("YTD_HOST", "0.0.0.0"),
        help="Host/interface to bind (default: 0.0.0.0 or YTD_HOST env).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("YTD_PORT", "8765")),
        help="Port to bind (default: 8765 or YTD_PORT env). Use 0 to auto-pick a free port.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    class ReusableThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True

    try:
        server = ReusableThreadingHTTPServer((args.host, args.port), DownloadHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                "启动失败：端口已被占用（Address already in use）。\n"
                f"- 当前尝试绑定：{args.host}:{args.port}\n"
                "- 解决办法：\n"
                "  1) 换一个端口：`python3 whisper/tool_ytd.py --port 8766`\n"
                "  2) 或让系统自动选空闲端口：`python3 whisper/tool_ytd.py --port 0`\n"
                "  3) 或查出是谁占用了端口并结束它：`ss -ltnp | rg ':8765'`（把 8765 换成你的端口）\n"
                "- 也可能是你之前启动的同一个脚本还在后台运行。"
            )
            raise SystemExit(2) from exc
        raise

    host, port = server.server_address[:2]
    print(f"Serving  downloader on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
