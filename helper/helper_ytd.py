#!/usr/bin/env python3
"""Reusable yt-dlp download helpers.

Used by: ytd.py, w/p_ytd.py

Evaluation by:
- evaluation_ytd.py
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

TRACKING_KEYS = {"fbclid", "feature", "pp", "si", "t", "xmt"}
PLATFORM_X = "x/twitter"
PLATFORM_META = "meta"
PLATFORM_THREADS = "threads"
PLATFORM_YOUTUBE = "youtube"
X_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
X_DOMAINS = ("x.com", "twitter.com")
YOUTUBE_DOMAINS = (
    "youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
)
META_DOMAINS = (
    "facebook.com",
    "instagram.com",
)
THREADS_DOMAINS = (
    "threads.com",
    "threads.net",
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


def classify_url(url):
    url = str(url or "").strip()
    if url and "://" not in url:
        url = "https://" + url.lstrip("/")
    host = urlsplit(url).netloc.lower().split(":", 1)[0]
    for platform, domains in (
        (PLATFORM_X, X_DOMAINS),
        (PLATFORM_YOUTUBE, YOUTUBE_DOMAINS),
        (PLATFORM_META, META_DOMAINS),
        (PLATFORM_THREADS, THREADS_DOMAINS),
    ):
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return platform
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
    url = str(url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    parts = urlsplit(url)
    scheme = parts.scheme or "https"
    host = parts.netloc.lower()

    def build_url(path, query=""):
        return urlunsplit((scheme, parts.netloc, path, query, ""))

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        if parts.path == "/watch":
            video_id = parse_qs(parts.query).get("v", [""])[0].strip()
            return build_url("/watch", urlencode({"v": video_id}) if video_id else "")
        return build_url(parts.path.rstrip("/"))
    if "youtu.be" in host:
        return build_url(parts.path.rstrip("/"))
    filtered = [(k, v) for k, vs in parse_qs(parts.query, keep_blank_values=True).items()
                for v in vs if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS]
    return build_url(parts.path, urlencode(filtered, doseq=True))


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


def _decode_embedded_threads_url(value):
    value = unescape(value).replace("\\/", "/")
    try:
        value = json.loads(f'"{value}"')
    except json.JSONDecodeError:
        pass
    return value.replace("\\u0026", "&").replace("\\u003d", "=").replace("&amp;", "&")


def _iter_threads_media_urls(html_text):
    text = unescape(html_text).replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
    patterns = (
        r'<meta[^>]+property=["\']og:video(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::secure_url)?["\']',
        r'"(?:video_url|playable_url|src|url)"\s*:\s*"([^"]+?(?:\.mp4|\.m3u8)[^"]*)"',
        r'(https?://[^"\'<>\s]+?(?:\.mp4|\.m3u8)[^"\'<>\s]*)',
    )
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            media_url = _decode_embedded_threads_url(match.group(1))
            if media_url.startswith("http") and media_url not in seen:
                seen.add(media_url)
                yield media_url


def _resolve_threads_video_url(url, timeout):
    print(f"Resolving Threads URL: {url}")
    request = Request(url, headers={"User-Agent": X_USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=timeout) as response:
            html_text = response.read().decode("utf-8", "replace")
    except OSError as exc:
        raise RuntimeError("解析 Threads 链接失败，请确认链接可访问。") from exc

    for media_url in _iter_threads_media_urls(html_text):
        print(f"Resolved Threads media URL: {media_url}")
        return media_url
    raise RuntimeError("Threads 链接已识别，但页面中没有找到可下载的视频地址。")


def _x_command(url, temp_dir, resolve_timeout):
    auth_token, ct0 = _x_auth()
    url = _resolve_x_url(url, auth_token, ct0, resolve_timeout)
    return url, [
        "yt-dlp",
        "--newline",
        *X_FORMAT_ARGS,
        "-o",
        "%(uploader)s_%(id)s.%(ext)s",
        *build_common_args(include_cookie_sources=False),
        "--cookies",
        _write_x_cookies(temp_dir, auth_token, ct0),
        url,
    ]


def _threads_command(url, mode, resolve_timeout):
    media_url = _resolve_threads_video_url(url, resolve_timeout)
    if mode not in FORMATS:
        raise RuntimeError("无效下载模式。")
    return media_url, [
        "yt-dlp",
        "--newline",
        "--no-playlist",
        *FORMATS[mode],
        "-o",
        "%(title).50s.%(ext)s",
        media_url,
    ]


def _generic_ytdlp_command(url, mode):
    if mode not in FORMATS:
        raise RuntimeError("无效下载模式。")
    return url, [
        "yt-dlp",
        "--newline",
        "--no-playlist",
        *FORMATS[mode],
        "-o",
        "%(title).50s.%(ext)s",
        *build_common_args(),
        url,
    ]


def build_download_command(url, mode, temp_dir, resolve_timeout):
    platform = classify_url(url)
    if platform == PLATFORM_X:
        return _x_command(url, temp_dir, resolve_timeout)
    if platform == PLATFORM_THREADS:
        return _threads_command(url, mode, resolve_timeout)
    if platform in (PLATFORM_YOUTUBE, PLATFORM_META):
        return _generic_ytdlp_command(url, mode)
    raise RuntimeError(f"Unsupported URL: {url}")


def run_yt_dlp(cmd, temp_dir):
    print("Running:", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, cwd=temp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("系统里找不到 yt-dlp。") from exc
    lines = []
    progress = -1
    assert proc.stdout is not None

    def _update_progress(line, progress):
        m = re.search(r"(\d+(?:\.\d+)?)%", line)
        if m:
            p = int(float(m.group(1)))
            if p != progress:
                done = 40 * p // 100
                print(f"\r[{'█'*done}{'-'*(40-done)}] {p:3d}%", end="", flush=True)
                return p
        return progress

    for raw in proc.stdout:
        line = raw.strip()
        if line:
            lines.append(line)
            if "[download]" in line:
                progress = _update_progress(line, progress)
    proc.stdout.close()
    if progress >= 0:
        print()
    if proc.wait():
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("yt-dlp failed:\n" + ("\n".join(lines[-10:]) or "Unknown yt-dlp error."))
    files = [p for p in Path(temp_dir).iterdir() if p.is_file()]
    if files:
        return max(files, key=lambda p: p.stat().st_mtime), temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)
    raise RuntimeError("下载完成，但没有生成文件。")


def move_download_to_output_dir(path, temp_dir, output_dir):
    if output_dir is None:
        return path, temp_dir
    t = Path(output_dir)
    t.mkdir(parents=True, exist_ok=True)
    target = t / path.name
    i = 1
    while target.exists() and i < 1000000:
        target = t / f"{path.stem}_{i}{path.suffix}"
        i += 1
    shutil.move(os.fspath(path), os.fspath(target))
    try:
        s = t.stat()
        os.chown(target, s.st_uid, s.st_gid)
        os.chmod(target, 0o664)
    except Exception:
        pass
    shutil.rmtree(temp_dir, ignore_errors=True)
    return target, None


def download(url, mode, output_dir=None, resolve_timeout=20):
    original_url = url
    if not (url := clean_url(url)):
        raise RuntimeError(f"Invalid URL: {original_url}")
    temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    try:
        url, cmd = build_download_command(url, mode, temp_dir, resolve_timeout)
        print(f"Download request: {url} ({classify_url(url) or mode})")
        return move_download_to_output_dir(*run_yt_dlp(cmd, temp_dir), output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _try_download_ttml_for_lang(lang, url, output_dir):
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
    print(f"Trying TTML subtitle language: {lang}")
    try:
        proc = subprocess.run(cmd, cwd=temp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ttml_files = sorted(Path(temp_dir).glob("*.ttml"), key=lambda p: p.stat().st_mtime, reverse=True)
        if ttml_files:
            print(f"Using TTML subtitle language: {lang}")
            return move_download_to_output_dir(ttml_files[0], temp_dir, output_dir)
        tail = "\n".join((proc.stdout or "").splitlines()[-5:])
        print(f"No TTML for {lang}: {tail or 'yt-dlp failed'}" if proc.returncode else f"No TTML for {lang}")
    except Exception as exc:
        print(f"No TTML for {lang}: {exc}")
    finally:
        if output_dir is not None or not any(Path(temp_dir).glob("*.ttml")):
            shutil.rmtree(temp_dir, ignore_errors=True)
    return None


def _try_download_ttml(url, output_dir=None):
    langs = [x.strip() for x in os.getenv("YTD_SUB_LANGS", "zh-Hans,zh-Hant,zh-HK,yue,zh,en,ja").split(",") if x.strip()]
    for lang in langs:
        if result := _try_download_ttml_for_lang(lang, url, output_dir):
            return result
    print("TTML unavailable for all preferred languages, fallback to video.")
    return None


def download_ttml_or_video(url, mode="worst", output_dir=None, resolve_timeout=20):
    url = clean_url(url)
    if classify_url(url) == PLATFORM_YOUTUBE and (res := _try_download_ttml(url, output_dir=output_dir)):
        return res
    return download(url or url, mode, output_dir, resolve_timeout)