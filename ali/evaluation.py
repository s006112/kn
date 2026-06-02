#!/usr/bin/env python3
"""Isolated evaluator for ali_fetch.py and ali_send.py."""

from __future__ import annotations

import sys
import time
import unittest
import warnings
from email.message import EmailMessage as StdEmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch
from unittest.signals import registerResult

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ali.ali_send import (  # noqa: E402
    _add_admin_bcc,
    _build_message,
    _build_subject,
    _build_to_address,
    _require_reply_to_forward_sender,
    send_reply,
)
from ali import ali_fetch  # noqa: E402
from ali.ali_fetch import (  # noqa: E402
    _build_client,
    _fetch_records,
    _is_allowed_sender,
    _raw_to_email_message,
    _should_bypass_admin,
    fetch_new_messages,
    fetch_sender_replies,
)
from helper.utils_imap_client import RawFetchedRecord  # noqa: E402
from helper.utils_imap_config import ImapConfig, SmtpConfig  # noqa: E402
from helper.utils_imap_types import EmailMessage  # noqa: E402


class EmojiTextTestResult(unittest.TextTestResult):
    def startTest(self, test) -> None:
        target_file = getattr(test, "target_file", "")
        if target_file != getattr(self, "_target_file", ""):
            self.stream.writeln(f"\n========== {target_file} ==========")
            self._target_file = target_file
        super().startTest(test)

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


def _imap_config(**overrides: object) -> ImapConfig:
    values = {
        "host": "imap.example.com",
        "port": 993,
        "user": "ali@example.com",
        "password": "secret",
        "folder": "INBOX",
        "verify_ssl": True,
        "timeout": 30,
    }
    values.update(overrides)
    return ImapConfig(**values)


def _raw_email(uid: int = 7, **headers: str) -> RawFetchedRecord:
    values = {
        "From": "Reviewer <reviewer@ampco.com.hk>",
        "To": "ali@example.com",
        "Cc": "archive@example.com",
        "Subject": "Customer question",
        "Message-ID": "<message@example.com>",
    }
    values.update(headers)
    header_text = "\n".join(f"{name}: {value}" for name, value in values.items() if value)
    return RawFetchedRecord(
        uid=uid,
        flags=[],
        internaldate=None,
        raw_bytes=f"{header_text}\n\nBody text".encode(),
    )


class AdminBypassTests(unittest.TestCase):
    target_file = "ali_fetch.py"

    def test_admin_bypass_is_off_without_admin_email(self) -> None:
        with patch("ali.ali_fetch.dotenv_values", return_value={}):
            self.assertFalse(_should_bypass_admin("admin@ampco.com.hk"))

    def test_admin_bypass_skips_matching_email_when_debug_is_false(self) -> None:
        env = {"ADMIN_USERNAME": "admin@ampco.com.hk", "ALI_DEBUG_MODE": "false"}
        with patch("ali.ali_fetch.dotenv_values", return_value=env):
            self.assertTrue(_should_bypass_admin("ADMIN@AMPCO.COM.HK"))

    def test_admin_bypass_processes_email_when_debug_is_true(self) -> None:
        env = {"ADMIN_USERNAME": "admin@ampco.com.hk", "ALI_DEBUG_MODE": "true"}
        with patch("ali.ali_fetch.dotenv_values", return_value=env):
            self.assertFalse(_should_bypass_admin("admin@ampco.com.hk"))

    def test_admin_bypass_processes_nonmatching_email(self) -> None:
        env = {"ADMIN_USERNAME": "admin@ampco.com.hk", "ALI_DEBUG_MODE": "false"}
        with patch("ali.ali_fetch.dotenv_values", return_value=env):
            self.assertFalse(_should_bypass_admin("reviewer@ampco.com.hk"))

    def test_admin_bypass_defaults_debug_to_true(self) -> None:
        env = {"ADMIN_USERNAME": "admin@ampco.com.hk"}
        with patch("ali.ali_fetch.dotenv_values", return_value=env):
            self.assertFalse(_should_bypass_admin("admin@ampco.com.hk"))


class AllowedSenderTests(unittest.TestCase):
    target_file = "ali_fetch.py"

    def test_sender_allows_company_email(self) -> None:
        self.assertTrue(_is_allowed_sender("reviewer@ampco.com.hk"))

    def test_sender_allows_company_email_ignoring_case(self) -> None:
        self.assertTrue(_is_allowed_sender("REVIEWER@AMPCO.COM.HK"))

    def test_sender_rejects_empty_email(self) -> None:
        self.assertFalse(_is_allowed_sender(""))

    def test_sender_rejects_ali_email_ignoring_case(self) -> None:
        self.assertFalse(_is_allowed_sender("ALI@AMPCO.COM.HK"))

    def test_sender_rejects_external_email(self) -> None:
        self.assertFalse(_is_allowed_sender("customer@example.com"))

    def test_sender_rejects_spoofed_company_suffix(self) -> None:
        self.assertFalse(_is_allowed_sender("reviewer@fakeampco.com.hk"))


class BuildFetchClientTests(unittest.TestCase):
    target_file = "ali_fetch.py"

    def test_client_fails_without_imap_config(self) -> None:
        with (
            patch("ali.ali_fetch.load_imap_config", return_value=None),
            self.assertRaisesRegex(RuntimeError, "IMAP configuration missing"),
        ):
            _build_client(MagicMock(), require_credentials=True)

    def test_client_connects_from_imap_config(self) -> None:
        logger = MagicMock()
        client = MagicMock()
        config = _imap_config(folder="Reviews")
        with (
            patch("ali.ali_fetch.load_imap_config", return_value=config) as load_config,
            patch("ali.ali_fetch.ImapClient", return_value=client) as imap_client,
        ):
            result = _build_client(logger, require_credentials=True)

        load_config.assert_called_once_with("IMAP_FOLDER", "INBOX", require_credentials=True)
        imap_client.assert_called_once_with(
            server="imap.example.com",
            port=993,
            user="ali@example.com",
            password="secret",
            verify_ssl=True,
            timeout=30,
            logger=logger,
        )
        client.connect.assert_called_once_with()
        self.assertEqual(result, (client, "Reviews"))


class FetchPipelineTests(unittest.TestCase):
    target_file = "ali_fetch.py"

    def setUp(self) -> None:
        self.client = MagicMock()
        self.logger = MagicMock()

    def test_new_messages_filter_admin_external_ali_and_review_thread(self) -> None:
        records = [MagicMock(uid=uid) for uid in range(1, 6)]
        emails = [
            _email(from_addr="admin@ampco.com.hk"),
            _email(from_addr="customer@example.com"),
            _email(from_addr="ali@ampco.com.hk"),
            _email(from_addr="reviewer@ampco.com.hk", subject="Question [ALI:v1]"),
            _email(from_addr="reviewer@ampco.com.hk", subject="Question"),
        ]
        with (
            patch("ali.ali_fetch.configure_logging", return_value=self.logger),
            patch("ali.ali_fetch._build_client", return_value=(self.client, "INBOX")),
            patch("ali.ali_fetch._fetch_records", return_value=records) as fetch_records,
            patch("ali.ali_fetch._raw_to_email_message", side_effect=emails),
            patch("ali.ali_fetch._should_bypass_admin", side_effect=[True, False, False, False, False]),
        ):
            result = fetch_new_messages(max_messages=5)

        fetch_records.assert_called_once_with(self.client, "INBOX", ["UNSEEN"])
        self.assertEqual(result, [emails[4]])
        self.assertEqual(
            self.client.move_message.call_args_list,
            [unittest.mock.call("INBOX", 2, "Trash"), unittest.mock.call("INBOX", 3, "Trash")],
        )
        self.client.disconnect.assert_called_once_with()

    def test_new_messages_apply_limit_after_filtering(self) -> None:
        records = [MagicMock(uid=uid) for uid in range(1, 4)]
        emails = [
            _email(from_addr="customer@example.com"),
            _email(from_addr="first@ampco.com.hk"),
            _email(from_addr="second@ampco.com.hk"),
        ]
        with (
            patch("ali.ali_fetch.configure_logging", return_value=self.logger),
            patch("ali.ali_fetch._build_client", return_value=(self.client, "INBOX")),
            patch("ali.ali_fetch._fetch_records", return_value=records),
            patch("ali.ali_fetch._raw_to_email_message", side_effect=emails) as parse_message,
            patch("ali.ali_fetch._should_bypass_admin", return_value=False),
        ):
            result = fetch_new_messages(max_messages=1)

        self.assertEqual(result, [emails[1]])
        self.assertEqual(parse_message.call_count, 2)
        self.client.disconnect.assert_called_once_with()

    def test_new_messages_disconnect_when_fetch_fails(self) -> None:
        with (
            patch("ali.ali_fetch.configure_logging", return_value=self.logger),
            patch("ali.ali_fetch._build_client", return_value=(self.client, "INBOX")),
            patch("ali.ali_fetch._fetch_records", side_effect=RuntimeError("offline")),
            self.assertRaisesRegex(RuntimeError, "offline"),
        ):
            fetch_new_messages()

        self.client.disconnect.assert_called_once_with()

    def test_replies_return_empty_when_review_query_is_empty(self) -> None:
        with (
            patch("ali.ali_fetch.configure_logging", return_value=self.logger),
            patch("ali.ali_fetch._build_client", return_value=(self.client, "INBOX")),
            patch.object(ali_fetch, "REVIEW_SUBJECT_IMAP_QUERY", ""),
        ):
            result = fetch_sender_replies()

        self.assertEqual(result, [])
        self.client.disconnect.assert_called_once_with()

    def test_replies_filter_admin_external_and_ali_email(self) -> None:
        records = [MagicMock(uid=uid) for uid in range(1, 5)]
        emails = [
            _email(from_addr="admin@ampco.com.hk"),
            _email(from_addr="customer@example.com"),
            _email(from_addr="ali@ampco.com.hk"),
            _email(from_addr="reviewer@ampco.com.hk"),
        ]
        with (
            patch("ali.ali_fetch.configure_logging", return_value=self.logger),
            patch("ali.ali_fetch._build_client", return_value=(self.client, "INBOX")),
            patch("ali.ali_fetch._fetch_records", return_value=records) as fetch_records,
            patch("ali.ali_fetch._raw_to_email_message", side_effect=emails),
            patch("ali.ali_fetch._should_bypass_admin", side_effect=[True, False, False, False]),
        ):
            result = fetch_sender_replies()

        fetch_records.assert_called_once_with(
            self.client,
            "INBOX",
            ["UNSEEN", "SUBJECT", ali_fetch.REVIEW_SUBJECT_IMAP_QUERY],
        )
        self.assertEqual(result, [emails[3]])
        self.assertEqual(
            self.client.move_message.call_args_list,
            [unittest.mock.call("INBOX", 2, "Trash"), unittest.mock.call("INBOX", 3, "Trash")],
        )
        self.client.disconnect.assert_called_once_with()

    def test_replies_disconnect_when_fetch_fails(self) -> None:
        with (
            patch("ali.ali_fetch.configure_logging", return_value=self.logger),
            patch("ali.ali_fetch._build_client", return_value=(self.client, "INBOX")),
            patch("ali.ali_fetch._fetch_records", side_effect=RuntimeError("offline")),
            self.assertRaisesRegex(RuntimeError, "offline"),
        ):
            fetch_sender_replies()

        self.client.disconnect.assert_called_once_with()


class FetchRecordsTests(unittest.TestCase):
    target_file = "ali_fetch.py"

    def test_records_return_empty_without_matching_uids(self) -> None:
        client = MagicMock()
        client.search_uids.return_value = []

        self.assertEqual(_fetch_records(client, "INBOX", ["UNSEEN"]), [])
        client.fetch_batch.assert_not_called()

    def test_records_fetch_all_matching_uids(self) -> None:
        client = MagicMock()
        client.search_uids.return_value = [1, 2]
        client.fetch_batch.return_value = ["first", "second"]

        result = _fetch_records(client, "INBOX", ["UNSEEN"])

        self.assertEqual(result, ["first", "second"])
        client.fetch_batch.assert_called_once_with("INBOX", [1, 2])

    def test_records_apply_limit_before_fetch(self) -> None:
        client = MagicMock()
        client.search_uids.return_value = [1, 2, 3]

        _fetch_records(client, "INBOX", ["UNSEEN"], limit=2)

        client.fetch_batch.assert_called_once_with("INBOX", [1, 2])


class FetchRawEmailParsingTests(unittest.TestCase):
    target_file = "ali_fetch.py"

    def test_raw_email_parses_headers_and_body(self) -> None:
        message = _raw_to_email_message(_raw_email())

        self.assertEqual(message.uid, 7)
        self.assertEqual(message.message_id, "<message@example.com>")
        self.assertEqual(message.from_addr, "reviewer@ampco.com.hk")
        self.assertEqual(message.to_addrs, ["ali@example.com"])
        self.assertEqual(message.cc_addrs, ["archive@example.com"])
        self.assertEqual(message.subject, "Customer question")
        self.assertEqual(message.body_text, "Body text")

    def test_raw_email_handles_missing_optional_headers(self) -> None:
        message = _raw_to_email_message(
            _raw_email(To="", Cc="", Subject="", **{"Message-ID": ""})
        )

        self.assertEqual(message.to_addrs, [])
        self.assertEqual(message.cc_addrs, [])
        self.assertEqual(message.subject, "")
        self.assertEqual(message.message_id, "")

    def test_raw_email_parses_multiple_addresses(self) -> None:
        message = _raw_to_email_message(
            _raw_email(To="ali@example.com, archive@example.com", Cc="copy@example.com")
        )

        self.assertEqual(message.to_addrs, ["ali@example.com", "archive@example.com"])
        self.assertEqual(message.cc_addrs, ["copy@example.com"])

    def test_raw_html_email_has_empty_plain_body(self) -> None:
        record = RawFetchedRecord(
            uid=7,
            flags=[],
            internaldate=None,
            raw_bytes=b"From: reviewer@ampco.com.hk\nContent-Type: text/html\n\n<p>Body</p>",
        )

        self.assertEqual(_raw_to_email_message(record).body_text, "")


class SubjectTests(unittest.TestCase):
    target_file = "ali_send.py"

    def test_subject_adds_re_prefix(self) -> None:
        self.assertEqual(_build_subject("Customer question"), "Re: Customer question")

    def test_subject_keeps_existing_re_prefix(self) -> None:
        self.assertEqual(_build_subject("  RE: Customer question"), "  RE: Customer question")

    def test_empty_subject_becomes_re_prefix(self) -> None:
        self.assertEqual(_build_subject(""), "Re:")


class RecipientGuardTests(unittest.TestCase):
    target_file = "ali_send.py"

    def test_recipient_removes_display_name(self) -> None:
        self.assertEqual(
            _build_to_address("Reviewer Name <reviewer@example.com>"),
            "reviewer@example.com",
        )

    def test_recipient_keeps_plain_email(self) -> None:
        self.assertEqual(_build_to_address("reviewer@example.com"), "reviewer@example.com")

    def test_recipient_empty_email_stays_empty(self) -> None:
        self.assertEqual(_build_to_address(""), "")

    def test_recipient_keeps_unparsed_text_for_guard_rejection(self) -> None:
        self.assertEqual(_build_to_address("Reviewer <>"), "Reviewer <>")

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

    def test_recipient_rejects_missing_target_email(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Missing sender or recipient"):
            _require_reply_to_forward_sender("reviewer@example.com", "")

    def test_recipient_rejects_invalid_email(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unable to parse email address"):
            _require_reply_to_forward_sender("Reviewer <>", "reviewer@example.com")

    def test_recipient_rejects_invalid_target_email(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unable to parse email address"):
            _require_reply_to_forward_sender("reviewer@example.com", "Reviewer <>")


class SenderAdminBccTests(unittest.TestCase):
    target_file = "ali_send.py"

    def test_admin_bcc_is_added_for_non_admin_sender(self) -> None:
        msg = StdEmailMessage()
        env = {"ADMIN_USERNAME": " Admin@Example.COM "}
        with patch("ali.ali_send.dotenv_values", return_value=env):
            _add_admin_bcc(msg, "Reviewer <reviewer@example.com>")

        self.assertEqual(msg["Bcc"], "admin@example.com")

    def test_admin_bcc_is_skipped_for_admin_sender(self) -> None:
        msg = StdEmailMessage()
        env = {"ADMIN_USERNAME": " Admin@Example.COM "}
        with patch("ali.ali_send.dotenv_values", return_value=env):
            _add_admin_bcc(msg, "Administrator <ADMIN@example.com>")

        self.assertIsNone(msg["Bcc"])

    def test_admin_bcc_is_skipped_without_admin_email(self) -> None:
        msg = StdEmailMessage()
        with patch("ali.ali_send.dotenv_values", return_value={}):
            _add_admin_bcc(msg, "reviewer@example.com")

        self.assertIsNone(msg["Bcc"])


class MessageConstructionTests(unittest.TestCase):
    target_file = "ali_send.py"

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

    def test_message_uses_original_email_when_reply_is_empty(self) -> None:
        msg = _build_message(_email(), "", "ali@example.com")

        self.assertTrue(msg.get_content().startswith("-----Original Message-----\n"))
        self.assertNotIn("\n\n\n-----Original Message-----", msg.get_content())

    def test_message_skips_empty_original_headers(self) -> None:
        msg = _build_message(
            _email(from_addr="", to_addrs=[], subject="", body_text="Original body"),
            "Internal review",
            "ali@example.com",
        )

        self.assertEqual(
            msg.get_content(),
            "Internal review\n\n-----Original Message-----\n\n> Original body\n",
        )

    def test_message_empty_reply_and_original_body_stays_empty(self) -> None:
        msg = _build_message(_email(body_text=""), "", "ali@example.com")

        self.assertEqual(msg.get_content(), "\n")

    def test_message_strips_whitespace_and_lists_original_recipients(self) -> None:
        msg = _build_message(
            _email(to_addrs=["ali@example.com", "archive@example.com"], body_text="  Body  "),
            "  Review  ",
            "ali@example.com",
        )

        self.assertIn("Review\n\n-----Original Message-----", msg.get_content())
        self.assertIn("To: ali@example.com, archive@example.com", msg.get_content())
        self.assertTrue(msg.get_content().endswith("> Body\n"))


class SendReplyTests(unittest.TestCase):
    target_file = "ali_send.py"

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
        with load_env as load_env_mock, configure_logging, load_config as load_config_mock:
            result = send_reply(_email(), "Internal review")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_message, "Missing SMTP_HOST/USER/PASSWORD")
        load_env_mock.assert_called_once_with()
        load_config_mock.assert_called_once_with()
        self.append_sent.assert_not_called()

    def test_send_uses_custom_from_email(self) -> None:
        config = _smtp_config()
        load_env, configure_logging, load_config = self._common_patches(config)
        with (
            load_env,
            configure_logging,
            load_config,
            patch("ali.ali_send.smtplib.SMTP", return_value=self.server),
        ):
            result = send_reply(_email(), "Internal review", from_addr="custom@example.com")

        self.assertTrue(result.ok)
        sent_msg = self.server.send_message.call_args.args[0]
        self.assertEqual(sent_msg["From"], "custom@example.com")

    def test_send_uses_plain_smtp_without_starttls(self) -> None:
        config = _smtp_config(use_starttls=False)
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
        self.server.starttls.assert_not_called()

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

    def test_send_returns_failure_when_smtp_connection_fails(self) -> None:
        config = _smtp_config()
        load_env, configure_logging, load_config = self._common_patches(config)
        with (
            load_env,
            configure_logging,
            load_config,
            patch("ali.ali_send.smtplib.SMTP", side_effect=RuntimeError("offline")),
        ):
            result = send_reply(_email(), "Internal review")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_message, "send_reply failed (see logs)")
        self.logger.exception.assert_called_once()
        self.append_sent.assert_not_called()

    def test_send_returns_failure_when_saving_sent_email_fails(self) -> None:
        config = _smtp_config()
        self.append_sent.side_effect = RuntimeError("imap unavailable")
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
        self.server.send_message.assert_called_once()
        self.logger.exception.assert_called_once()

    def test_send_rejects_missing_reviewer_before_smtp(self) -> None:
        config = _smtp_config()
        load_env, configure_logging, load_config = self._common_patches(config)
        with (
            load_env,
            configure_logging,
            load_config,
            patch("ali.ali_send.smtplib.SMTP") as smtp,
            self.assertRaisesRegex(RuntimeError, "Missing sender or recipient"),
        ):
            send_reply(_email(from_addr=""), "Internal review")

        smtp.assert_not_called()
        self.append_sent.assert_not_called()


if __name__ == "__main__":
    unittest.main(testRunner=EmojiTextTestRunner, verbosity=2)
