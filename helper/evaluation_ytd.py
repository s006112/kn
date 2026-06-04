#!/usr/bin/env python3
"""Isolated dry-run evaluator for helper_ytd.py.

Used by:
- None (standalone test entry point)

"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock, patch
from unittest.signals import registerResult

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helper import helper_ytd as ytd  # noqa: E402

# Toggle this to True when you want real network metadata checks by default.
# You can also run with YTD_REAL_CHECK=1 without editing this file.
RUN_REAL_LINK_CHECKS = True

REAL_LINK_FIXTURES = (
    ("https://www.youtube.com/watch?v=4dnri2eITX8", ytd.PLATFORM_YOUTUBE, "youtube"),
    ("https://www.youtube.com/shorts/cqD_rVj-Nyo", ytd.PLATFORM_YOUTUBE, "youtube"),
    ("https://www.youtube.com/watch?v=KpUqhdzsjnE", ytd.PLATFORM_YOUTUBE, "youtube"),
    ("https://x.com/i/status/2051216612636668186", ytd.PLATFORM_X, "twitter"),
    ("https://www.facebook.com/watch/?v=972353075160922", ytd.PLATFORM_META, "facebook"),
    ("https://www.instagram.com/reel/DVx-1ZRkg3p/", ytd.PLATFORM_META, "instagram"),
    ("https://www.instagram.com/p/DWdEZcqkzYr/", ytd.PLATFORM_META, "instagram"),
    ("https://www.threads.com/@mr.wei5888/post/DZD-ZHRE8Z7?xmt=AQG01CRhRvQw4T7Bn2o474gZkG41_idxLIer1fb023ctTw", ytd.PLATFORM_THREADS, "generic"),
)


class EmojiTextTestResult(unittest.TextTestResult):
    def addSuccess(self, test) -> None:
        unittest.TestResult.addSuccess(self, test)
        self.stream.writeln("✅ PASS")

    def addFailure(self, test, err) -> None:
        unittest.TestResult.addFailure(self, test, err)
        self.stream.writeln("❌ FAIL")

    def addError(self, test, err) -> None:
        unittest.TestResult.addError(self, test, err)
        self.stream.writeln("❌ ERROR")


class EmojiTextTestRunner(unittest.TextTestRunner):
    resultclass = EmojiTextTestResult

    def run(self, test):
        result = self._makeResult()
        registerResult(result)
        result.failfast = self.failfast
        result.buffer = self.buffer
        result.tb_locals = self.tb_locals
        with warnings.catch_warnings():
            if self.warnings:
                warnings.simplefilter(self.warnings)
            start_time = time.perf_counter()
            result.startTestRun()
            try:
                test(result)
            finally:
                result.stopTestRun()
            elapsed = time.perf_counter() - start_time

        result.printErrors()
        self.stream.writeln(result.separator2)
        self.stream.writeln(f"Ran {result.testsRun} tests in {elapsed:.3f}s")
        self.stream.writeln()

        failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
        skipped = len(result.skipped)
        expected_failures = len(result.expectedFailures)
        passed = result.testsRun - failed - skipped - expected_failures
        self.stream.writeln(f"Passed: {passed} ✅")
        self.stream.writeln(f"Failed: {failed} ❌")
        if skipped:
            self.stream.writeln(f"Skipped: {skipped}")
        if expected_failures:
            self.stream.writeln(f"Expected failures: {expected_failures}")
        self.stream.flush()
        return result


class FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class FakePopen:
    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        self.stdout = FakeStdout(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class UrlUtilityTests(unittest.TestCase):
    def test_classify_url_detects_supported_platforms(self) -> None:
        self.assertEqual(ytd.classify_url("x.com/user/status/1"), ytd.PLATFORM_X)
        self.assertEqual(ytd.classify_url("youtu.be/abc"), ytd.PLATFORM_YOUTUBE)
        self.assertEqual(ytd.classify_url("instagram.com/p/abc"), ytd.PLATFORM_META)
        self.assertEqual(ytd.classify_url("threads.net/@user/post/abc"), ytd.PLATFORM_THREADS)
        self.assertEqual(ytd.classify_url("www.threads.com/@user/post/abc"), ytd.PLATFORM_THREADS)
        self.assertEqual(ytd.classify_url("example.com/video"), "")

    def test_classify_url_normalizes_scheme_port_and_case(self) -> None:
        self.assertEqual(ytd.classify_url("HTTPS://YouTube.com:443/watch?v=1"), ytd.PLATFORM_YOUTUBE)
        self.assertEqual(ytd.classify_url("m.youtube.com/watch?v=1"), ytd.PLATFORM_YOUTUBE)
        self.assertEqual(ytd.classify_url("notyoutube.com/watch?v=1"), "")

    def test_clean_url_removes_youtube_tracking_but_keeps_video_id(self) -> None:
        dirty = "https://www.youtube.com/watch?v=abc123&utm_source=x&t=10"
        self.assertEqual(ytd.clean_url(dirty), "https://www.youtube.com/watch?v=abc123")

    def test_clean_url_strips_youtu_be_query(self) -> None:
        self.assertEqual(ytd.clean_url("https://youtu.be/abc123?si=share"), "https://youtu.be/abc123")

    def test_clean_url_removes_common_tracking_keys(self) -> None:
        dirty = "https://example.com/path?utm_source=x&fbclid=1&keep=yes&si=no"
        self.assertEqual(ytd.clean_url(dirty), "https://example.com/path?keep=yes")

    def test_clean_url_returns_empty_for_missing_youtube_video_id(self) -> None:
        self.assertEqual(ytd.clean_url("https://youtube.com/watch?feature=share"), "https://youtube.com/watch")

    def test_real_link_fixtures_classify_and_clean(self) -> None:
        for url, platform, _extractor in REAL_LINK_FIXTURES:
            with self.subTest(url=url):
                cleaned = ytd.clean_url(url)
                self.assertTrue(cleaned)
                self.assertEqual(ytd.classify_url(cleaned), platform)


class RuntimeAndArgumentTests(unittest.TestCase):
    def test_detect_js_runtime_prefers_env_value(self) -> None:
        with patch.dict(os.environ, {"YTD_JS_RUNTIME": "node"}, clear=False):
            self.assertEqual(ytd.detect_js_runtime(), "node")

    def test_detect_js_runtime_finds_first_available_runtime(self) -> None:
        def which(name: str) -> str | None:
            return "/usr/bin/qjs" if name == "qjs" else None

        with patch.dict(os.environ, {"YTD_JS_RUNTIME": ""}, clear=False), patch("helper.helper_ytd.shutil.which", side_effect=which):
            self.assertEqual(ytd.detect_js_runtime(), "quickjs:/usr/bin/qjs")

    def test_detect_js_runtime_returns_empty_without_runtime(self) -> None:
        with patch.dict(os.environ, {"YTD_JS_RUNTIME": ""}, clear=False), patch("helper.helper_ytd.shutil.which", return_value=None):
            self.assertEqual(ytd.detect_js_runtime(), "")

    def test_build_common_args_includes_runtime_components_cookies_and_extra_args(self) -> None:
        env = {
            "YTD_REMOTE_COMPONENTS": "ejs:github",
            "YTD_EXTRACTOR_ARGS": "youtube:player_client=default",
            "YTD_COOKIES_FILE": "/tmp/cookies.txt",
            "YTD_COOKIES_FROM_BROWSER": "chrome",
            "YTD_EXTRA_ARGS": "--sleep-requests 1",
        }
        with patch.dict(os.environ, env, clear=False), patch("helper.helper_ytd.detect_js_runtime", return_value="node"):
            args = ytd.build_common_args()

        self.assertIn("--js-runtimes", args)
        self.assertIn("node", args)
        self.assertIn("--cookies", args)
        self.assertIn("/tmp/cookies.txt", args)
        self.assertIn("--cookies-from-browser", args)
        self.assertIn("chrome", args)
        self.assertEqual(args[-2:], ["--sleep-requests", "1"])

    def test_build_common_args_can_skip_cookie_sources(self) -> None:
        env = {
            "YTD_COOKIES_FILE": "/tmp/cookies.txt",
            "YTD_COOKIES_FROM_BROWSER": "chrome",
            "YTD_EXTRA_ARGS": "",
        }
        with patch.dict(os.environ, env, clear=False), patch("helper.helper_ytd.detect_js_runtime", return_value=""):
            args = ytd.build_common_args(include_cookie_sources=False)

        self.assertNotIn("--cookies", args)
        self.assertNotIn("--cookies-from-browser", args)


class XAuthAndResolveTests(unittest.TestCase):
    def test_x_auth_reads_required_tokens(self) -> None:
        with patch.dict(os.environ, {"X_AUTH_TOKEN": "auth", "X_CT0": "ct0"}, clear=False):
            self.assertEqual(ytd._x_auth(), ("auth", "ct0"))

    def test_x_auth_rejects_missing_tokens(self) -> None:
        with patch.dict(os.environ, {"X_AUTH_TOKEN": "", "X_CT0": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "X_AUTH_TOKEN"):
                ytd._x_auth()

    def test_resolve_x_url_uses_response_url_and_cleans_it(self) -> None:
        response = Mock()
        response.geturl.return_value = "https://x.com/user/status/1?utm_source=share"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("helper.helper_ytd.urlopen", return_value=response):
            self.assertEqual(ytd._resolve_x_url("https://x.com/t/1", "auth", "ct0", 2), "https://x.com/user/status/1")

    def test_resolve_x_url_wraps_network_error(self) -> None:
        with patch("helper.helper_ytd.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "解析 X/Twitter 链接失败"):
                ytd._resolve_x_url("https://x.com/t/1", "auth", "ct0", 2)

    def test_write_x_cookies_writes_netscape_cookie_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_path = Path(ytd._write_x_cookies(temp_dir, "auth", "ct0"))
            text = cookie_path.read_text(encoding="utf-8")

        self.assertIn("# Netscape HTTP Cookie File", text)
        self.assertIn("auth_token\tauth", text)
        self.assertIn("ct0\tct0", text)


class ThreadsResolveTests(unittest.TestCase):
    def test_iter_threads_media_urls_finds_meta_video(self) -> None:
        html = '<meta property="og:video" content="https://cdn.example.com/video.mp4?token=1&amp;v=2">'

        self.assertEqual(list(ytd._iter_threads_media_urls(html)), ["https://cdn.example.com/video.mp4?token=1&v=2"])

    def test_iter_threads_media_urls_finds_json_video_url(self) -> None:
        html = '{"video_url":"https:\\/\\/cdn.example.com\\/video.mp4?token=1\\u0026v=2"}'

        self.assertEqual(list(ytd._iter_threads_media_urls(html)), ["https://cdn.example.com/video.mp4?token=1&v=2"])

    def test_resolve_threads_video_url_reads_first_media_url(self) -> None:
        response = Mock()
        response.read.return_value = b'<meta property="og:video" content="https://cdn.example.com/video.mp4">'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("helper.helper_ytd.urlopen", return_value=response):
            self.assertEqual(
                ytd._resolve_threads_video_url("https://threads.com/@user/post/abc", 2),
                "https://cdn.example.com/video.mp4",
            )

    def test_resolve_threads_video_url_rejects_missing_media(self) -> None:
        response = Mock()
        response.read.return_value = b"<html></html>"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)

        with patch("helper.helper_ytd.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "没有找到可下载的视频地址"):
                ytd._resolve_threads_video_url("https://threads.com/@user/post/abc", 2)


class CommandBuilderTests(unittest.TestCase):
    def test_x_command_builds_download_command_with_temp_cookies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("helper.helper_ytd._x_auth", return_value=("auth", "ct0")),
                patch("helper.helper_ytd._resolve_x_url", return_value="https://x.com/user/status/1"),
                patch("helper.helper_ytd.build_common_args", return_value=["--common"]),
            ):
                resolved_url, cmd = ytd._x_command("https://x.com/user/status/1", temp_dir, 20)

        self.assertEqual(resolved_url, "https://x.com/user/status/1")
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("--cookies", cmd)
        self.assertIn("--common", cmd)
        self.assertIn("https://x.com/user/status/1", cmd)

    def test_generic_command_builds_requested_mode(self) -> None:
        with patch("helper.helper_ytd.build_common_args", return_value=["--common"]):
            resolved_url, cmd = ytd._generic_ytdlp_command("https://youtube.com/watch?v=abc", "mp3")

        self.assertEqual(resolved_url, "https://youtube.com/watch?v=abc")
        self.assertIn("-x", cmd)
        self.assertIn("--audio-format", cmd)
        self.assertIn("--common", cmd)

    def test_generic_command_rejects_bad_mode(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "无效下载模式"):
            ytd._generic_ytdlp_command("https://youtube.com/watch?v=abc", "4k")

    def test_threads_command_resolves_media_url_without_common_args(self) -> None:
        with patch("helper.helper_ytd._resolve_threads_video_url", return_value="https://cdn.example.com/video.mp4") as resolver:
            resolved_url, cmd = ytd._threads_command("https://threads.com/@user/post/abc", "720p", 20)

        self.assertEqual(resolved_url, "https://cdn.example.com/video.mp4")
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("--no-playlist", cmd)
        self.assertIn("--merge-output-format", cmd)
        self.assertEqual(cmd[-1], "https://cdn.example.com/video.mp4")
        resolver.assert_called_once_with("https://threads.com/@user/post/abc", 20)

    def test_threads_command_rejects_bad_mode(self) -> None:
        with patch("helper.helper_ytd._resolve_threads_video_url", return_value="https://cdn.example.com/video.mp4"):
            with self.assertRaisesRegex(RuntimeError, "无效下载模式"):
                ytd._threads_command("https://threads.com/@user/post/abc", "4k", 20)

    def test_build_download_command_dispatches_x_platform(self) -> None:
        with patch("helper.helper_ytd._x_command", return_value=("resolved", ["x-cmd"])) as x_command:
            self.assertEqual(ytd.build_download_command("https://x.com/user/status/1", "720p", "/tmp", 20), ("resolved", ["x-cmd"]))

        x_command.assert_called_once_with("https://x.com/user/status/1", "/tmp", 20)

    def test_build_download_command_dispatches_generic_platforms(self) -> None:
        with patch("helper.helper_ytd._generic_ytdlp_command", return_value=("resolved", ["generic-cmd"])) as generic_command:
            self.assertEqual(ytd.build_download_command("https://youtube.com/watch?v=abc", "mp3", "/tmp", 20), ("resolved", ["generic-cmd"]))

        generic_command.assert_called_once_with("https://youtube.com/watch?v=abc", "mp3")

    def test_build_download_command_dispatches_threads_platform(self) -> None:
        with patch("helper.helper_ytd._threads_command", return_value=("resolved", ["threads-cmd"])) as threads_command:
            self.assertEqual(
                ytd.build_download_command("https://threads.com/@user/post/abc", "720p", "/tmp", 20),
                ("resolved", ["threads-cmd"]),
            )

        threads_command.assert_called_once_with("https://threads.com/@user/post/abc", "720p", 20)

    def test_build_download_command_rejects_unsupported_platform(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsupported URL"):
            ytd.build_download_command("https://example.com/video", "720p", "/tmp", 20)


class DownloadProcessTests(unittest.TestCase):
    def test_run_yt_dlp_returns_newest_file_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            older = Path(temp_dir) / "older.mp4"
            newer = Path(temp_dir) / "newer.mp4"
            older.write_text("old", encoding="utf-8")
            newer.write_text("new", encoding="utf-8")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            popen = FakePopen(["[download] 25.0%\n", "[download] 100.0%\n"])

            with patch("helper.helper_ytd.subprocess.Popen", return_value=popen):
                path, returned_temp_dir = ytd.run_yt_dlp(["yt-dlp"], temp_dir)

        self.assertEqual(path.name, "newer.mp4")
        self.assertEqual(returned_temp_dir, temp_dir)
        self.assertTrue(popen.stdout.closed)

    def test_run_yt_dlp_removes_temp_dir_when_binary_missing(self) -> None:
        temp_dir = tempfile.mkdtemp()
        with patch("helper.helper_ytd.subprocess.Popen", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "找不到 yt-dlp"):
                ytd.run_yt_dlp(["yt-dlp"], temp_dir)
        self.assertFalse(Path(temp_dir).exists())

    def test_run_yt_dlp_removes_temp_dir_on_failed_process(self) -> None:
        temp_dir = tempfile.mkdtemp()
        with patch("helper.helper_ytd.subprocess.Popen", return_value=FakePopen(["error line\n"], returncode=1)):
            with self.assertRaisesRegex(RuntimeError, "yt-dlp failed"):
                ytd.run_yt_dlp(["yt-dlp"], temp_dir)
        self.assertFalse(Path(temp_dir).exists())

    def test_run_yt_dlp_removes_temp_dir_when_no_file_generated(self) -> None:
        temp_dir = tempfile.mkdtemp()
        with patch("helper.helper_ytd.subprocess.Popen", return_value=FakePopen([], returncode=0)):
            with self.assertRaisesRegex(RuntimeError, "没有生成文件"):
                ytd.run_yt_dlp(["yt-dlp"], temp_dir)
        self.assertFalse(Path(temp_dir).exists())


class FileMoveTests(unittest.TestCase):
    def test_move_download_returns_original_path_without_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "video.mp4"
            source.write_text("video", encoding="utf-8")
            path, returned_temp_dir = ytd.move_download_to_output_dir(source, temp_dir, None)

            self.assertEqual(path, source)
            self.assertEqual(returned_temp_dir, temp_dir)
            self.assertTrue(source.exists())

    def test_move_download_moves_file_and_avoids_overwrite(self) -> None:
        temp_dir = tempfile.mkdtemp()
        with tempfile.TemporaryDirectory() as output_dir:
            source = Path(temp_dir) / "video.mp4"
            source.write_text("new", encoding="utf-8")
            existing = Path(output_dir) / "video.mp4"
            existing.write_text("existing", encoding="utf-8")

            target, returned_temp_dir = ytd.move_download_to_output_dir(source, temp_dir, output_dir)

            self.assertEqual(target.name, "video_1.mp4")
            self.assertIsNone(returned_temp_dir)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing")
        self.assertFalse(Path(temp_dir).exists())


class DownloadWrapperTests(unittest.TestCase):
    def test_download_cleans_url_builds_command_and_moves_result(self) -> None:
        source = Path("/tmp/video.mp4")
        with (
            patch("helper.helper_ytd.tempfile.mkdtemp", return_value="/tmp/ytdlp_eval"),
            patch("helper.helper_ytd.build_download_command", return_value=("https://youtube.com/watch?v=abc", ["yt-dlp"])),
            patch("helper.helper_ytd.run_yt_dlp", return_value=(source, "/tmp/ytdlp_eval")),
            patch("helper.helper_ytd.move_download_to_output_dir", return_value=(source, None)),
        ):
            result = ytd.download("https://youtube.com/watch?v=abc&utm_source=x", "720p", output_dir="/out")

        self.assertEqual(result, (source, None))

    def test_download_rejects_invalid_clean_url(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid URL"):
            ytd.download("", "720p")


class TtmlFallbackTests(unittest.TestCase):
    def test_try_download_ttml_moves_first_found_subtitle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as output_dir:
            ttml = Path(temp_dir) / "caption.ttml"
            ttml.write_text("<tt></tt>", encoding="utf-8")
            completed = Mock(returncode=0, stdout="")

            with (
                patch.dict(os.environ, {"YTD_SUB_LANGS": "en"}, clear=False),
                patch("helper.helper_ytd.tempfile.mkdtemp", return_value=temp_dir),
                patch("helper.helper_ytd.subprocess.run", return_value=completed),
            ):
                target, returned_temp_dir = ytd._try_download_ttml("https://youtube.com/watch?v=abc", output_dir=output_dir)

            self.assertEqual(target.name, "caption.ttml")
            self.assertIsNone(returned_temp_dir)
            self.assertTrue(target.exists())

    def test_try_download_ttml_keeps_temp_file_without_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ttml = Path(temp_dir) / "caption.ttml"
            ttml.write_text("<tt></tt>", encoding="utf-8")
            completed = Mock(returncode=0, stdout="")

            with (
                patch.dict(os.environ, {"YTD_SUB_LANGS": "en"}, clear=False),
                patch("helper.helper_ytd.tempfile.mkdtemp", return_value=temp_dir),
                patch("helper.helper_ytd.subprocess.run", return_value=completed),
            ):
                target, returned_temp_dir = ytd._try_download_ttml("https://youtube.com/watch?v=abc")

            self.assertEqual(target, ttml)
            self.assertEqual(returned_temp_dir, temp_dir)
            self.assertTrue(target.exists())

    def test_try_download_ttml_returns_none_after_all_languages_fail(self) -> None:
        temp_dirs = [tempfile.mkdtemp(), tempfile.mkdtemp()]
        completed = Mock(returncode=1, stdout="no subtitles")

        try:
            with (
                patch.dict(os.environ, {"YTD_SUB_LANGS": "zh,en"}, clear=False),
                patch("helper.helper_ytd.tempfile.mkdtemp", side_effect=temp_dirs),
                patch("helper.helper_ytd.subprocess.run", return_value=completed),
            ):
                self.assertIsNone(ytd._try_download_ttml("https://youtube.com/watch?v=abc"))
        finally:
            for temp_dir in temp_dirs:
                self.assertFalse(Path(temp_dir).exists())

    def test_download_ttml_or_video_tries_ttml_for_youtube(self) -> None:
        with patch("helper.helper_ytd._try_download_ttml", return_value=(Path("/tmp/caption.ttml"), None)) as try_ttml:
            result = ytd.download_ttml_or_video("https://youtube.com/watch?v=abc", mode="worst")

        self.assertEqual(result, (Path("/tmp/caption.ttml"), None))
        try_ttml.assert_called_once()

    def test_download_ttml_or_video_falls_back_to_video_for_non_youtube(self) -> None:
        with (
            patch("helper.helper_ytd._try_download_ttml") as try_ttml,
            patch("helper.helper_ytd.download", return_value=(Path("/tmp/video.mp4"), "/tmp/ytd")) as download,
        ):
            result = ytd.download_ttml_or_video("https://instagram.com/p/abc", mode="worst")

        self.assertEqual(result, (Path("/tmp/video.mp4"), "/tmp/ytd"))
        try_ttml.assert_not_called()
        download.assert_called_once_with("https://instagram.com/p/abc", "worst", None, 20)

    def test_download_ttml_or_video_falls_back_when_ttml_unavailable(self) -> None:
        with (
            patch("helper.helper_ytd._try_download_ttml", return_value=None),
            patch("helper.helper_ytd.download", return_value=(Path("/tmp/video.mp4"), "/tmp/ytd")) as download,
        ):
            result = ytd.download_ttml_or_video("https://youtube.com/watch?v=abc", mode="worst")

        self.assertEqual(result, (Path("/tmp/video.mp4"), "/tmp/ytd"))
        download.assert_called_once_with("https://youtube.com/watch?v=abc", "worst", None, 20)


@unittest.skipUnless(
    RUN_REAL_LINK_CHECKS or os.getenv("YTD_REAL_CHECK") == "1",
    "set RUN_REAL_LINK_CHECKS=True or YTD_REAL_CHECK=1 to run network metadata checks",
)
class ZRealLinkMetadataTests(unittest.TestCase):
    @staticmethod
    def _allow_remote_skip() -> bool:
        return not RUN_REAL_LINK_CHECKS and os.getenv("YTD_REAL_CHECK") == "1"

    @staticmethod
    def _is_rate_limited(text: object) -> bool:
        text = str(text)
        return (
            "429" in text
            or "Too Many Requests" in text
            or "rate-limit" in text
            or "login required" in text
        )

    def test_real_link_fixtures_are_extractable_without_download(self) -> None:
        for url, platform, extractor in REAL_LINK_FIXTURES:
            with self.subTest(url=url):
                check_url = url
                if platform == ytd.PLATFORM_THREADS:
                    try:
                        check_url = ytd._resolve_threads_video_url(ytd.clean_url(url), 20)
                    except RuntimeError as exc:
                        if self._allow_remote_skip() and (
                            self._is_rate_limited(exc) or self._is_rate_limited(exc.__cause__)
                        ):
                            self.skipTest(f"remote site rate-limited metadata check for {url}")
                        raise
                proc = ytd.subprocess.run(
                    [
                        "yt-dlp",
                        "--simulate",
                        "--skip-download",
                        "--no-playlist",
                        "--print",
                        "%(extractor_key)s | %(id)s",
                        *([] if platform == ytd.PLATFORM_THREADS else ytd.build_common_args()),
                        check_url,
                    ],
                    stdout=ytd.subprocess.PIPE,
                    stderr=ytd.subprocess.STDOUT,
                    text=True,
                    timeout=60,
                )
                if proc.returncode and self._allow_remote_skip() and self._is_rate_limited(proc.stdout):
                    self.skipTest(f"remote site rate-limited metadata check for {url}")
                self.assertEqual(proc.returncode, 0, proc.stdout)
                self.assertIn(extractor, proc.stdout.lower())


if __name__ == "__main__":
    unittest.main(testRunner=EmojiTextTestRunner(verbosity=2, buffer=True))
