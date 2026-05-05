#!/usr/bin/env python3
"""Reusable yt-dlp download helpers."""

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
PLATFORM_X = "x/twitter"
PLATFORM_YTDLP = "yt-dlp"
X_DOMAINS = ("x.com", "twitter.com")
YTDLP_DOMAINS = ("youtube.com", "youtube-nocookie.com", "youtu.be")


def _host_matches(host, domain):
    return host == domain or host.endswith(f".{domain}")


def _url_host(url):
    url = url.strip()
    parsed = urlsplit(url if "://" in url else "https://" + url.lstrip("/"))
    return parsed.netloc.lower().split(":", 1)[0]


def classify_url(url):
    host = _url_host(url)
    if any(_host_matches(host, domain) for domain in X_DOMAINS):
        return PLATFORM_X
    if any(_host_matches(host, domain) for domain in YTDLP_DOMAINS):
        return PLATFORM_YTDLP
    return ""


def detect_js_runtime():
    if runtime := os.getenv("YTD_JS_RUNTIME", "").strip():
        return runtime
    for name in ("deno", "node", "bun", "qjs", "quickjs"):
        if path := shutil.which(name):
            return "quickjs" if name == "quickjs" else f"quickjs:{path}" if name == "qjs" else name
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
    args = [item for flag, value in pairs if value for item in (flag, value)]
    return args + shlex.split(os.getenv("YTD_EXTRA_ARGS", ""))


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


def resolve_download_url_list_file(list_file):
    path = Path(list_file)
    if path.exists() or path.name != "x.txt":
        return path
    uppercase_path = path.with_name("X.txt")
    return uppercase_path if uppercase_path.exists() else path


def read_next_download_url(list_file, skipped_urls):
    path = resolve_download_url_list_file(list_file)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                url = line.strip()
                if url and url not in skipped_urls and classify_url(url):
                    return url, path
    except FileNotFoundError:
        return None, path
    return None, path


def get_x_auth():
    auth_token = os.getenv("X_AUTH_TOKEN", "").strip()
    ct0 = os.getenv("X_CT0", "").strip()
    if not auth_token or not ct0:
        raise RuntimeError("缺少 X/Twitter 下载所需环境变量：X_AUTH_TOKEN 和 X_CT0。请先在环境变量或 .env 中设置。")
    return auth_token, ct0


def resolve_x_url(url, auth_token, ct0, timeout=20):
    print(f"Resolving X/Twitter URL: {url}")
    request = Request(
        url,
        headers={
            "User-Agent": X_USER_AGENT,
            "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
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
        cookie_path = Path(temp_dir) / ".cookies" / "x_cookies.txt"
        cookie_path.parent.mkdir(exist_ok=True)
        cookie_path.write_text(cookie_content, encoding="utf-8")
        return os.fspath(cookie_path)
    except OSError as exc:
        raise RuntimeError("创建 X/Twitter 临时 cookie 文件失败。") from exc


def build_generic_yt_dlp_command(url, mode):
    return [
        "yt-dlp",
        "--newline",
        *FORMATS[mode],
        "-o",
        "%(title).50s.%(ext)s",
        *build_common_args(),
        url,
    ]


def build_x_yt_dlp_command(url, cookie_path):
    return [
        "yt-dlp",
        "--newline",
        *X_FORMAT_ARGS,
        "-o",
        "%(uploader)s_%(id)s.%(ext)s",
        *build_common_args(include_cookie_sources=False),
        "--cookies",
        cookie_path,
        url,
    ]


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


def move_download_to_output_dir(path, temp_dir, output_dir):
    if output_dir is None:
        return path, temp_dir

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    stem, suffix = target.stem, target.suffix
    counter = 1
    while target.exists():
        target = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        shutil.move(os.fspath(path), os.fspath(target))

        try:
            parent_stat = target_dir.stat()
            os.chown(target, parent_stat.st_uid, parent_stat.st_gid)
        except (PermissionError, AttributeError):
            pass

        try:
            os.chmod(target, 0o664)
        except PermissionError:
            pass

        return target, None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def download_with_yt_dlp(url, mode, output_dir=None):
    if mode not in FORMATS:
        raise RuntimeError("无效下载模式。")
    temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    try:
        cmd = build_generic_yt_dlp_command(url, mode)
        print(f"Download request: {url} ({mode})")
        path, temp_dir = run_yt_dlp(cmd, temp_dir)
        return move_download_to_output_dir(path, temp_dir, output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def download_x_twitter(url, output_dir=None, resolve_timeout=20):
    temp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    try:
        auth_token, ct0 = get_x_auth()
        resolved_url = resolve_x_url(url, auth_token, ct0, timeout=resolve_timeout)
        cookie_path = create_x_cookie_file(temp_dir, auth_token, ct0)
        cmd = build_x_yt_dlp_command(resolved_url, cookie_path)
        print(f"Download request: {resolved_url} (x/twitter)")
        path, temp_dir = run_yt_dlp(cmd, temp_dir)
        return move_download_to_output_dir(path, temp_dir, output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def download(url, mode, output_dir=None, resolve_timeout=20):
    platform = classify_url(url)
    if platform == PLATFORM_X:
        return download_x_twitter(
            url,
            output_dir=output_dir,
            resolve_timeout=resolve_timeout,
        )
    if platform == PLATFORM_YTDLP:
        return download_with_yt_dlp(url, mode, output_dir=output_dir)
    raise RuntimeError(f"Unsupported URL: {url}")


def download_url_to_folder(url, output_dir, resolve_timeout=20):
    cleaned_url = clean_url(url)
    if not cleaned_url:
        raise RuntimeError(f"Invalid URL: {url}")
    platform = classify_url(cleaned_url)
    if platform == PLATFORM_X:
        path, _ = download_x_twitter(
            cleaned_url,
            output_dir=output_dir,
            resolve_timeout=resolve_timeout,
        )
    elif platform == PLATFORM_YTDLP:
        path, _ = download_with_yt_dlp(cleaned_url, "720p", output_dir=output_dir)
    else:
        raise RuntimeError(f"Unsupported URL: {url}")
    return cleaned_url, path


def remove_download_url_line(list_file, url):
    path = resolve_download_url_list_file(list_file)
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False

    for index, line in enumerate(lines):
        if line.strip() != url:
            continue

        del lines[index]
        with path.open("w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
        return True

    return False
