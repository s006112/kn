#!/usr/bin/env python3
"""Reusable yt-dlp download helpers.

Used by: ytd.py, w/p_ytd.py

Evaluation by:
- evaluation_ytd.py
"""

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

TRACKING_KEYS = {"fbclid", "feature", "pp", "si", "t"}
PLATFORM_X = "x/twitter"
PLATFORM_YTDLP = "yt-dlp"
PLATFORM_YOUTUBE = "youtube"
X_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
X_DOMAINS = ("x.com", "twitter.com")
YOUTUBE_DOMAINS = (
    "youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
)
YTDLP_DOMAINS = (
    "facebook.com",
    "instagram.com",
)
FORMATS = {
    "worst": ["-f", "(worstvideo[ext=mp4]+worstaudio[ext=m4a])/(worstvideo+worstaudio)/worst"],
    "720p": [
        "-f",
        "(bestvideo[ext=mp4][height=720]+bestaudio[ext=m4a])/"
        "(bestvideo[height=720]+bestaudio)/"
        "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a])/"
        "(bestvideo[height<=720]+bestaudio)/best[height<=720]/best",
        "--merge-output-format",
        "mp4",
    ],
    "mp3": ["-x", "--audio-format", "mp3", "-f", "bestaudio/best"],
}
X_FORMAT_ARGS = ["-f", "http-2176/bestvideo+bestaudio/best", "--merge-output-format", "mp4"]


def _with_scheme(url):
    url = str(url or "").strip()
    return "https://" + url.lstrip("/") if url and "://" not in url else url


def _host(url):
    return urlsplit(_with_scheme(url)).netloc.lower().split(":", 1)[0]


def _host_in(host, domains):
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def classify_url(url):
    host = _host(url)
    if _host_in(host, X_DOMAINS):
        return PLATFORM_X
    if _host_in(host, YOUTUBE_DOMAINS):
        return PLATFORM_YOUTUBE
    if _host_in(host, YTDLP_DOMAINS):
        return PLATFORM_YTDLP
    return ""


def detect_js_runtime():
    if runtime := os.getenv("YTD_JS_RUNTIME", "").strip():
        return runtime
    for name in ("deno", "node", "bun", "qjs", "quickjs"):
        if path := shutil.which(name):
            if name == "quickjs":
                return "quickjs"
            return f"quickjs:{path}" if name == "qjs" else name
    return ""


def build_common_args(include_cookie_sources=True):
    pairs = [
        ("--js-runtimes", detect_js_runtime()),
        ("--remote-components", os.getenv("YTD_REMOTE_COMPONENTS", "ejs:github").strip()),
        ("--extractor-args", os.getenv("YTD_EXTRACTOR_ARGS", "youtube:player_client=default").strip()),
    ]
    if include_cookie_sources:
        pairs += [
            ("--cookies", os.getenv("YTD_COOKIES_FILE", "").strip()),
            ("--cookies-from-browser", os.getenv("YTD_COOKIES_FROM_BROWSER", "").strip()),
        ]
    return [x for flag, value in pairs if value for x in (flag, value)] + shlex.split(os.getenv("YTD_EXTRA_ARGS", ""))


def clean_url(url):
    if not (url := _with_scheme(url)):
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    scheme = parts.scheme or "https"
    if "youtu.be" in host:
        path, query = parts.path.rstrip("/"), ""
    elif "youtube.com" in host or "youtube-nocookie.com" in host:
        path, query = parts.path.rstrip("/"), ""
        if parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0].strip()
            if not video_id:
                return ""
            path, query = "/watch", urlencode({"v": video_id})
    else:
        path = parts.path
        query = urlencode(
            [
                (key, value)
                for key, values in parse_qs(parts.query, keep_blank_values=True).items()
                if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
                for value in values
            ],
            doseq=True,
        )
    return urlunsplit((scheme, parts.netloc, path, query, ""))
def _x_auth():
    auth_token = os.getenv("X_AUTH_TOKEN", "").strip()
    ct0 = os.getenv("X_CT0", "").strip()
    if not auth_token or not ct0:
        raise RuntimeError("缺少 X/Twitter 下载所需环境变量：X_AUTH_TOKEN 和 X_CT0。请先在环境变量或 .env 中设置。")
    return auth_token, ct0


def _resolve_x_url(url, auth_token, ct0, timeout):
    print(f"Resolving X/Twitter URL: {url}")
    request = Request(url, headers={"User-Agent": X_USER_AGENT, "Cookie": f"auth_token={auth_token}; ct0={ct0}"})
    try:
        with urlopen(request, timeout=timeout) as response:
            resolved_url = response.geturl()
    except OSError as exc:
        raise RuntimeError("解析 X/Twitter 链接失败，请确认链接可访问且当前认证信息有效。") from exc
    resolved_url = clean_url(resolved_url) or resolved_url
    print(f"Resolved X/Twitter URL: {resolved_url}")
    return resolved_url


def _write_x_cookies(temp_dir, auth_token, ct0):
    cookie_path = Path(temp_dir) / ".cookies" / "x_cookies.txt"
    cookie_path.parent.mkdir(exist_ok=True)
    try:
        cookie_path.write_text(
            "# Netscape HTTP Cookie File\n"
            f".x.com\tTRUE\t/\tTRUE\t2000000000\tauth_token\t{auth_token}\n"
            f".x.com\tTRUE\t/\tTRUE\t2000000000\tct0\t{ct0}\n"
            f".twitter.com\tTRUE\t/\tTRUE\t2000000000\tauth_token\t{auth_token}\n"
            f".twitter.com\tTRUE\t/\tTRUE\t2000000000\tct0\t{ct0}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError("创建 X/Twitter 临时 cookie 文件失败。") from exc
    return os.fspath(cookie_path)


def _command(url, mode, temp_dir, resolve_timeout):
    platform = classify_url(url)
    if platform not in (PLATFORM_X, PLATFORM_YOUTUBE, PLATFORM_YTDLP):
        raise RuntimeError(f"Unsupported URL: {url}")
    if platform == PLATFORM_X:
        auth_token, ct0 = _x_auth()
        url = _resolve_x_url(url, auth_token, ct0, resolve_timeout)
        args = [*X_FORMAT_ARGS, "-o", "%(uploader)s_%(id)s.%(ext)s", *build_common_args(include_cookie_sources=False), "--cookies", _write_x_cookies(temp_dir, auth_token, ct0)]
    else:
        if mode not in FORMATS:
            raise RuntimeError("无效下载模式。")
        args = ["--no-playlist", *FORMATS[mode], "-o", "%(title).50s.%(ext)s", *build_common_args()]
    return url, ["yt-dlp", "--newline", *args, url]


def run_yt_dlp(cmd, temp_dir):
    print("Running:", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, cwd=temp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("系统里找不到 yt-dlp。") from exc

    lines, shown = [], -1
    try:
        assert proc.stdout is not None
        def _update_progress(line, current):
            match = re.search(r"(\d+(?:\.\d+)?)%", line)
            if not match:
                return current
            pct = max(0, min(100, int(float(match.group(1)))))
            if pct != current:
                width = 40
                done = width * pct // 100
                print(f"\r[{'█' * done}{'-' * (width - done)}] {pct:3d}%", end="", flush=True)
                return pct
            return current
        for raw in proc.stdout:
            line = raw.strip()
            if line:
                lines.append(line)
            if "[download]" in line:
                shown = _update_progress(line, shown)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()

    error = None
    if proc.wait():
        error = "yt-dlp failed:\n" + ("\n".join(lines[-10:]) or "Unknown yt-dlp error.")
    else:
        if shown >= 0:
            print()
        files = [path for path in Path(temp_dir).iterdir() if path.is_file()]
        if files:
            return max(files, key=lambda path: path.stat().st_mtime), temp_dir
        error = "下载完成，但没有生成文件。"

    shutil.rmtree(temp_dir, ignore_errors=True)
    raise RuntimeError(error)


def move_download_to_output_dir(path, temp_dir, output_dir):
    if output_dir is None:
        return path, temp_dir

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    for i in range(1, 1000000):
        if not target.exists():
            break
        target = target_dir / f"{path.stem}_{i}{path.suffix}"

    try:
        shutil.move(os.fspath(path), os.fspath(target))
        try:
            stat = target_dir.stat()
            os.chown(target, stat.st_uid, stat.st_gid)
        except (PermissionError, AttributeError):
            pass
        try:
            os.chmod(target, 0o664)
        except PermissionError:
            pass
        return target, None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _download(url, mode="720p", output_dir=None, resolve_timeout=20):
    original_url = url
    if not (url := clean_url(url)):
        raise RuntimeError(f"Invalid URL: {original_url}")

    temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    url, cmd = _command(url, mode, temp_dir, resolve_timeout)
    print(f"Download request: {url} ({PLATFORM_X if classify_url(url) == PLATFORM_X else mode})")
    path, temp_dir = run_yt_dlp(cmd, temp_dir)
    return url, *move_download_to_output_dir(path, temp_dir, output_dir)

def download(url, mode, output_dir=None, resolve_timeout=20):
    _, path, temp_dir = _download(url, mode, output_dir=output_dir, resolve_timeout=resolve_timeout)
    return path, temp_dir

def _try_download_ttml(url, output_dir=None):
    langs = [x.strip() for x in os.getenv("YTD_SUB_LANGS", "zh-Hans,zh-Hant,zh-HK,yue,zh,en,ja").split(",") if x.strip()]

    for lang in langs:
        temp_dir = tempfile.mkdtemp(prefix="ytdlp_ttml_")
        cmd = [
            "yt-dlp",
            "--newline",
            "--skip-download",
            "--no-playlist",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            lang,
            "--sub-format",
            "ttml",
            "-o",
            "%(title).50s.%(ext)s",
            *build_common_args(),
            url,
        ]

        try:
            print(f"Trying TTML subtitle language: {lang}")
            print("Running:", " ".join(cmd))
            proc = subprocess.run(
                cmd,
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            ttml_files = sorted(Path(temp_dir).glob("*.ttml"), key=lambda p: p.stat().st_mtime, reverse=True)
            if ttml_files:
                print(f"Using TTML subtitle language: {lang}")
                return move_download_to_output_dir(ttml_files[0], temp_dir, output_dir)
            tail = "\n".join((proc.stdout or "").splitlines()[-5:])
            print(f"No TTML for {lang}: {tail or 'yt-dlp failed'}" if proc.returncode else f"No TTML for {lang}")
        except Exception as exc:
            print(f"No TTML for {lang}: {exc}")
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("TTML unavailable for all preferred languages, fallback to video.")
    return None


def download_ttml_or_video(url, mode="worst", output_dir=None, resolve_timeout=20):
    cleaned_url = clean_url(url)
    if cleaned_url and classify_url(cleaned_url) == PLATFORM_YOUTUBE:
        result = _try_download_ttml(cleaned_url, output_dir=output_dir)
        if result is not None:
            return result
    return download(cleaned_url or url, mode, output_dir=output_dir, resolve_timeout=resolve_timeout)
