#!/usr/bin/env python3
"""Isolated evaluator for ali.ali_send and its inbound sender guard."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ali.ali_send import (  # noqa: E402
    _build_message,
    _build_subject,
    _build_to_address,
    _require_reply_to_forward_sender,
    send_reply,
)
from ali.ali_fetch import _is_allowed_sender  # noqa: E402
from helper.utils_imap_config import SmtpConfig  # noqa: E402
from helper.utils_imap_types import EmailMessage  # noqa: E402


class EmojiTextTestResult(unittest.TextTestResult):
    def _write_marker(self, marker: str) -> None:
        self.stream.writeln(marker)

    def addSuccess(self, test) -> None:
        unittest.TestResult.addSuccess(self, test)
        self._write_marker("✅ PASS")

    def addFailure(self, test, err) -> None:
        unittest.TestResult.addFailure(self, test, err)
        self._write_marker("❌ FAIL")

    def addError(self, test, err) -> None:
        unittest.TestResult.addError(self, test, err)
        self._write_marker("❌ ERROR")


class EmojiTextTestRunner(unittest.TextTestRunner):
    resultclass = EmojiTextTestResult


def _email(**overrides: object) -> EmailMessage:
    values = {
        "uid": 7,
        "message_id": "<message@example.com>",
        "from_addr": "Reviewer Name <reviewer@example.com>",
        "to_addrs": ["ali@example.com"],
        "cc_addrs": [],
        "subject": "Customer question",
        "body_text": "First line\n\nSecond line",
        "raw_bytes": b"raw message",
    }
    values.update(overrides)
    return EmailMessage(**values)


def _smtp_config(**overrides: object) -> SmtpConfig:
    values = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "ali@example.com",
        "password": "secret",
        "use_ssl": False,
        "use_starttls": True,
        "default_from": "ali@example.com",
    }
    values.update(overrides)
    return SmtpConfig(**values)


class SubjectTests(unittest.TestCase):
    def test_subject_adds_re_prefix(self) -> None:
        self.assertEqual(_build_subject("Customer question"), "Re: Customer question")

    def test_subject_keeps_existing_re_prefix(self) -> None:
        self.assertEqual(_build_subject("  RE: Customer question"), "  RE: Customer question")

    def test_empty_subject_becomes_re_prefix(self) -> None:
        self.assertEqual(_build_subject(""), "Re:")


class RecipientGuardTests(unittest.TestCase):
    def test_recipient_removes_display_name(self) -> None:
        self.assertEqual(
            _build_to_address("Reviewer Name <reviewer@example.com>"),
            "reviewer@example.com",
        )

    def test_recipient_allows_same_email_ignoring_case(self) -> None:
        _require_reply_to_forward_sender(
            "Reviewer Name <Reviewer@Example.com>",
            "reviewer@example.com",
        )

    def test_recipient_rejects_different_email(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Outbound recipient mismatch"):
            _require_reply_to_forward_sender(
                "reviewer@example.com",
                "customer@example.com",
            )

    def test_recipient_rejects_missing_email(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Missing sender or recipient"):
            _require_reply_to_forward_sender("", "reviewer@example.com")

    def test_recipient_rejects_invalid_email(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unable to parse email address"):
            _require_reply_to_forward_sender("Reviewer <>", "reviewer@example.com")


class AllowedSenderTests(unittest.TestCase):
    def test_sender_allows_company_email(self) -> None:
        self.assertTrue(_is_allowed_sender("reviewer@ampco.com.hk"))

    def test_sender_rejects_external_email(self) -> None:
        self.assertFalse(_is_allowed_sender("customer@example.com"))

    def test_sender_rejects_ali_email_ignoring_case(self) -> None:
        self.assertFalse(_is_allowed_sender("ALI@AMPCO.COM.HK"))


class MessageConstructionTests(unittest.TestCase):
    def test_message_includes_reply_and_original_email(self) -> None:
        msg = _build_message(_email(), "Internal review\n", "ali@example.com")

        self.assertEqual(msg["From"], "ali@example.com")
        self.assertEqual(msg["To"], "reviewer@example.com")
        self.assertEqual(msg["Subject"], "Re: Customer question")
        self.assertEqual(msg["In-Reply-To"], "<message@example.com>")
        self.assertEqual(msg["References"], "<message@example.com>")
        self.assertEqual(
            msg.get_content(),
            "Internal review\n"
            "\n"
            "-----Original Message-----\n"
            "From: Reviewer Name <reviewer@example.com>\n"
            "To: ali@example.com\n"
            "Subject: Customer question\n"
            "\n"
            "> First line\n"
            ">\n"
            "> Second line\n",
        )

    def test_message_skips_thread_headers_without_message_id(self) -> None:
        msg = _build_message(
            _email(message_id="", body_text=""),
            "Internal review",
            "ali@example.com",
        )

        self.assertIsNone(msg["In-Reply-To"])
        self.assertIsNone(msg["References"])
        self.assertEqual(msg.get_content(), "Internal review\n")


class SendReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = MagicMock()
        self.server = MagicMock()
        self.server.__enter__.return_value = self.server
        append_sent_patcher = patch("ali.ali_send.append_to_imap_sent")
        self.append_sent = append_sent_patcher.start()
        self.addCleanup(append_sent_patcher.stop)

    def _common_patches(self, config: SmtpConfig | None):
        return (
            patch("ali.ali_send.load_env"),
            patch("ali.ali_send.configure_logging", return_value=self.logger),
            patch("ali.ali_send.load_smtp_config", return_value=config),
        )

    def test_send_fails_without_smtp_config(self) -> None:
        load_env, configure_logging, load_config = self._common_patches(None)
        with load_env, configure_logging, load_config:
            result = send_reply(_email(), "Internal review")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_message, "Missing SMTP_HOST/USER/PASSWORD")
        self.append_sent.assert_not_called()

    def test_send_uses_starttls_and_saves_sent_email(self) -> None:
        config = _smtp_config()
        load_env, configure_logging, load_config = self._common_patches(config)
        with (
            load_env,
            configure_logging,
            load_config,
            patch("ali.ali_send.smtplib.SMTP", return_value=self.server) as smtp,
        ):
            result = send_reply(_email(), "Internal review")

        self.assertTrue(result.ok)
        smtp.assert_called_once_with("smtp.example.com", 587, timeout=60)
        self.server.ehlo.assert_called()
        self.server.starttls.assert_called_once_with()
        self.server.login.assert_called_once_with("ali@example.com", "secret")
        sent_msg = self.server.send_message.call_args.args[0]
        self.assertEqual(sent_msg["To"], "reviewer@example.com")
        self.assertEqual(sent_msg["Reply-To"], "ali@example.com")
        self.append_sent.assert_called_once_with(sent_msg, self.logger)

    def test_send_uses_ssl_without_starttls(self) -> None:
        config = _smtp_config(port=465, use_ssl=True, use_starttls=True)
        load_env, configure_logging, load_config = self._common_patches(config)
        with (
            load_env,
            configure_logging,
            load_config,
            patch("ali.ali_send.smtplib.SMTP_SSL", return_value=self.server) as smtp_ssl,
        ):
            result = send_reply(_email(), "Internal review")

        self.assertTrue(result.ok)
        smtp_ssl.assert_called_once_with("smtp.example.com", 465, timeout=60)
        self.server.starttls.assert_not_called()

    def test_send_returns_failure_on_smtp_error(self) -> None:
        config = _smtp_config()
        self.server.login.side_effect = RuntimeError("authentication failed")
        load_env, configure_logging, load_config = self._common_patches(config)
        with (
            load_env,
            configure_logging,
            load_config,
            patch("ali.ali_send.smtplib.SMTP", return_value=self.server),
        ):
            result = send_reply(_email(), "Internal review")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_message, "send_reply failed (see logs)")
        self.logger.exception.assert_called_once()
        self.append_sent.assert_not_called()


if __name__ == "__main__":
    unittest.main(testRunner=EmojiTextTestRunner, verbosity=2)
