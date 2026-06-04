#!/usr/bin/env python3
"""
ALI isolated evaluator.

职责：
- 验证 ali_parse.py、ali_fetch.py、ali_llm.py 和 ali_send.py 的局部行为。

Used by:
- None（standalone test entry point）
"""

from __future__ import annotations

import sys
import runpy
import time
import unittest
import warnings
from datetime import datetime
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
from ali.ali_parse import (  # noqa: E402
    REVIEW_FOOTER_LINE,
    REVIEW_HEADER_LINE_TEMPLATE,
    ReviewState,
    extract_last_review_state,
    extract_reviewer_reply_text,
    normalize_email_input,
)
from ali import ali_fetch  # noqa: E402
from ali import ali_llm  # noqa: E402
from ali.ali_fetch import (  # noqa: E402
    _build_client,
    _fetch_records,
    _is_allowed_sender,
    _raw_to_email_message,
    _should_bypass_admin,
    fetch_new_messages,
    fetch_sender_replies,
)
from ali.ali_llm import (  # noqa: E402
    generate_review_package,
    rag_retrieval,
    render_review,
    route_email,
    step4_review,
)
from ali import ali_email  # noqa: E402
from ali.ali_email import (  # noqa: E402
    _build_review_subject,
    _default_poll_interval_minutes,
    _move_imap_message_to_failed,
    _phase1_new_messages,
    _phase2_sender_replies,
    _run_guarded,
    _send_internal_review,
)
from helper.utils_imap_client import RawFetchedRecord  # noqa: E402
from helper.utils_imap_config import ImapConfig, SmtpConfig  # noqa: E402
from helper.utils_imap_types import EmailMessage, SendResult  # noqa: E402


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
        self._write_marker("❌ FAIL")


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


class ParseNormalizeEmailInputTests(unittest.TestCase):
    target_file = "ali_parse.py"

    def test_normalize_email_input_trims_subject_and_body_edges(self) -> None:
        subject, body = normalize_email_input(
            _email(subject="  Customer question  ", body_text="\r\n\n  First\r\n\r\nSecond  \n\n")
        )

        self.assertEqual(subject, "Customer question")
        self.assertEqual(body, "First\n\nSecond")

    def test_normalize_email_input_preserves_interior_blank_lines(self) -> None:
        _, body = normalize_email_input(_email(body_text="One\n\n\nTwo"))

        self.assertEqual(body, "One\n\n\nTwo")

    def test_normalize_email_input_applies_max_body_len(self) -> None:
        _, body = normalize_email_input(_email(body_text="abcdef"), max_body_len=3)

        self.assertEqual(body, "abc")

    def test_normalize_email_input_can_disable_body_limit(self) -> None:
        _, body = normalize_email_input(_email(body_text="abcdef"), max_body_len=None)

        self.assertEqual(body, "abcdef")


class ParseReviewerReplyTests(unittest.TestCase):
    target_file = "ali_parse.py"

    def test_reply_text_returns_empty_for_empty_body(self) -> None:
        self.assertEqual(extract_reviewer_reply_text(""), "")

    def test_reply_text_stops_before_quoted_content(self) -> None:
        body = "Please revise this.\n\n> Original draft\n> More history"

        self.assertEqual(extract_reviewer_reply_text(body), "Please revise this.\n")

    def test_reply_text_stops_before_wrote_marker(self) -> None:
        body = "Looks good.\nOn Thu, Ali wrote:\nPrevious draft"

        self.assertEqual(extract_reviewer_reply_text(body), "Looks good.")

    def test_reply_text_stops_before_forward_marker(self) -> None:
        body = "FYI only\n-----Original Message-----\nFrom: Someone"

        self.assertEqual(extract_reviewer_reply_text(body), "FYI only")

    def test_reply_text_stops_before_forwarded_header_run(self) -> None:
        body = "Please use this.\nFrom: Ali\nSent: Today\nSubject: Draft"

        self.assertEqual(extract_reviewer_reply_text(body), "Please use this.")

    def test_reply_text_keeps_single_header_like_line_as_user_text(self) -> None:
        body = "Subject: please change tone\nThanks"

        self.assertEqual(extract_reviewer_reply_text(body), body)


class ParseReviewStateTests(unittest.TestCase):
    target_file = "ali_parse.py"

    def test_review_state_extracts_single_protocol_block(self) -> None:
        body = "\n".join(
            [
                REVIEW_HEADER_LINE_TEMPLATE.format(version=1),
                "Draft body",
                REVIEW_FOOTER_LINE,
            ]
        )

        self.assertEqual(
            extract_last_review_state(_email(body_text=body)),
            ReviewState(version=1, draft="Draft body"),
        )

    def test_review_state_selects_highest_version_block(self) -> None:
        body = "\n".join(
            [
                REVIEW_HEADER_LINE_TEMPLATE.format(version=1),
                "Old draft",
                REVIEW_FOOTER_LINE,
                REVIEW_HEADER_LINE_TEMPLATE.format(version=3),
                "New draft",
                REVIEW_FOOTER_LINE,
                REVIEW_HEADER_LINE_TEMPLATE.format(version=2),
                "Middle draft",
                REVIEW_FOOTER_LINE,
            ]
        )

        self.assertEqual(
            extract_last_review_state(_email(body_text=body)),
            ReviewState(version=3, draft="New draft"),
        )

    def test_review_state_handles_quoted_protocol_lines(self) -> None:
        body = "\n".join(
            [
                f"> {REVIEW_HEADER_LINE_TEMPLATE.format(version=2)}",
                "> Updated draft",
                f"> {REVIEW_FOOTER_LINE}",
            ]
        )

        self.assertEqual(
            extract_last_review_state(_email(body_text=body)),
            ReviewState(version=2, draft="Updated draft"),
        )

    def test_review_state_uses_remainder_without_footer(self) -> None:
        body = "\n".join(
            [
                REVIEW_HEADER_LINE_TEMPLATE.format(version=4),
                "Draft without footer",
            ]
        )

        self.assertEqual(
            extract_last_review_state(_email(body_text=body)),
            ReviewState(version=4, draft="Draft without footer"),
        )

    def test_review_state_raises_without_header(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cannot locate review header"):
            extract_last_review_state(_email(body_text="No protocol here"))


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


class LlmRagRetrievalTests(unittest.TestCase):
    target_file = "ali_llm.py"

    def test_route_email_maps_rag_categories(self) -> None:
        self.assertEqual(route_email("IEC certificate needed", ""), "safety")
        self.assertEqual(route_email("", "Please share testing standards"), "safety")
        self.assertEqual(route_email("Rita request", "Find the attachment"), "rita")

    def test_route_email_defaults_to_unknown(self) -> None:
        self.assertEqual(route_email("Pricing follow-up", "Can you confirm the delivery date?"), "unknown")

    def test_unmapped_category_skips_retrieval(self) -> None:
        with patch("ali.ali_llm.get_rag_engine") as get_engine:
            result = rag_retrieval("unknown", "Question", "Body", model="test-model")

        self.assertIsNone(result)
        get_engine.assert_not_called()

    def test_mapped_categories_use_configured_engine(self) -> None:
        engine = MagicMock()
        engine.answer_question.side_effect = [("First answer", ""), ("Second answer", "")]
        with patch("ali.ali_llm.get_rag_engine", return_value=engine) as get_engine:
            first = rag_retrieval("safety", "Question", "Body", model="test-model")
            second = rag_retrieval("safety", "", "Body only", model="test-model")

        self.assertEqual(first, "First answer")
        self.assertEqual(second, "Second answer")
        self.assertEqual(
            get_engine.call_args_list,
            [unittest.mock.call("standard"), unittest.mock.call("standard")],
        )
        self.assertEqual(
            engine.answer_question.call_args_list,
            [
                unittest.mock.call("Subject: Question\n\nBody", model="test-model"),
                unittest.mock.call("Body only", model="test-model"),
            ],
        )

    def test_rita_retrieval_uses_rita_engine_and_plain_query(self) -> None:
        engine = MagicMock()
        engine.answer_question.return_value = ("Historical answer", "")
        with patch("ali.ali_llm.get_rag_engine", return_value=engine) as get_engine:
            result = rag_retrieval(
                "rita",
                "Rita request",
                "Find the attachment",
                model="test-model",
            )

        query = engine.answer_question.call_args.args[0]
        self.assertEqual(result, "Historical answer")
        get_engine.assert_called_once_with("rita")
        self.assertEqual(query, "Subject: Rita request\n\nFind the attachment")

    def test_similarity_table_is_printed(self) -> None:
        engine = MagicMock()
        engine.answer_question.return_value = ("Answer", "score table")
        with (
            patch("ali.ali_llm.get_rag_engine", return_value=engine),
            patch("builtins.print") as print_mock,
        ):
            rag_retrieval("safety", "Question", "", model="test-model")

        print_mock.assert_called_once_with("\n[RAG] FAISS similarity table:\n\nscore table\n")

    def test_empty_answer_degrades_to_unused_context(self) -> None:
        engine = MagicMock()
        engine.answer_question.return_value = ("", "")
        with patch("ali.ali_llm.get_rag_engine", return_value=engine):
            result = rag_retrieval("safety", "", "", model="test-model")

        self.assertIsNone(result)
        engine.answer_question.assert_called_once_with("", model="test-model")

    def test_retrieval_failure_degrades_to_unused_context(self) -> None:
        with (
            patch("ali.ali_llm.get_rag_engine", side_effect=RuntimeError("offline")),
            patch("builtins.print") as print_mock,
        ):
            result = rag_retrieval("safety", "Question", "Body", model="test-model")

        self.assertIsNone(result)
        print_mock.assert_called_once_with("RAG Retrieval or Generation failed: offline")


class LlmGenerateReviewPackageTests(unittest.TestCase):
    target_file = "ali_llm.py"

    def test_phase_prompt_paths_are_distinct_and_named(self) -> None:
        self.assertEqual(ali_llm.P1_SYSTEM_PROMPT_PATH.name, "prompt_ali_p1_system.txt")
        self.assertEqual(ali_llm.P2_REVISION_PROMPT_PATH.name, "prompt_ali_p2_revision.txt")
        self.assertNotEqual(ali_llm.P1_SYSTEM_PROMPT_PATH, ali_llm.P2_REVISION_PROMPT_PATH)

    def test_v1_uses_rag_answer_without_calling_llm(self) -> None:
        category = "safety"
        p1_prompt_path = MagicMock()
        p2_prompt_path = MagicMock()
        with (
            patch("ali.ali_llm.route_email", return_value=category) as route_email,
            patch(
                "ali.ali_llm.rag_retrieval",
                return_value="RAG draft",
            ) as retrieve,
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            patch("ali.ali_llm.call_llm") as call_llm,
        ):
            result = generate_review_package(
                _email(subject="  Question  ", body_text="  Body  "),
                model="test-model",
            )

        self.assertEqual(result["draft"], "RAG draft")
        route_email.assert_called_once_with("Question", "Body")
        retrieve.assert_called_once_with(category, "Question", "Body", model="test-model")
        p1_prompt_path.read_text.assert_not_called()
        p2_prompt_path.read_text.assert_not_called()
        call_llm.assert_not_called()

    def test_v1_falls_back_to_prompt_llm_and_builds_package(self) -> None:
        p1_prompt_path = MagicMock()
        p1_prompt_path.read_text.return_value = "P1 prompt"
        p2_prompt_path = MagicMock()
        with (
            patch("ali.ali_llm.route_email", return_value="unknown"),
            patch(
                "ali.ali_llm.rag_retrieval",
                return_value=None,
            ),
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            patch("ali.ali_llm.call_llm", return_value="  LLM draft  ") as call_llm,
            patch("ali.ali_llm.step4_review", side_effect=lambda draft, **_: draft) as review,
        ):
            result = generate_review_package(
                _email(message_id="  <review-id>  ", subject=" Question ", body_text=" Body "),
                model="test-model",
                edit_version=3,
            )

        self.assertEqual(
            result,
            {
                "review_id": "<review-id>",
                "draft": "LLM draft",
                "allowed_actions": ["REPLY", "REJECT"],
                "version": 3,
            },
        )
        p1_prompt_path.read_text.assert_called_once_with(encoding="utf-8")
        p2_prompt_path.read_text.assert_not_called()
        call_llm.assert_called_once_with(
            model="test-model",
            system_prompt="P1 prompt",
            user_text="Subject: Question\n\nBody",
            file_path=None,
        )
        review.assert_called_once_with("LLM draft", enabled=False)

    def test_v1_missing_prompt_raises(self) -> None:
        p1_prompt_path = MagicMock()
        p1_prompt_path.read_text.side_effect = FileNotFoundError("Prompt file not found")
        p2_prompt_path = MagicMock()
        with (
            patch("ali.ali_llm.route_email", return_value="unknown"),
            patch(
                "ali.ali_llm.rag_retrieval",
                return_value=None,
            ),
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            self.assertRaisesRegex(FileNotFoundError, "Prompt file not found"),
        ):
            generate_review_package(
                _email(),
                model="test-model",
            )

        p1_prompt_path.read_text.assert_called_once_with(encoding="utf-8")
        p2_prompt_path.read_text.assert_not_called()

    def test_review_id_falls_back_to_uid(self) -> None:
        p1_prompt_path = MagicMock()
        p1_prompt_path.read_text.return_value = "System prompt"
        p2_prompt_path = MagicMock()
        with (
            patch("ali.ali_llm.route_email", return_value="unknown"),
            patch(
                "ali.ali_llm.rag_retrieval",
                return_value=None,
            ),
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            patch("ali.ali_llm.call_llm", return_value="Draft"),
        ):
            result = generate_review_package(
                _email(uid=42, message_id=" "),
                model="test-model",
            )

        self.assertEqual(result["review_id"], "42")
        p1_prompt_path.read_text.assert_called_once_with(encoding="utf-8")
        p2_prompt_path.read_text.assert_not_called()

    def test_v2_edits_previous_draft_and_bypasses_route_and_retrieval(self) -> None:
        p1_prompt_path = MagicMock()
        p2_prompt_path = MagicMock()
        p2_prompt_path.read_text.return_value = "P2 prompt"
        with (
            patch("ali.ali_llm.route_email") as route_email,
            patch("ali.ali_llm.rag_retrieval") as retrieve,
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            patch("ali.ali_llm.call_llm", return_value="  Edited draft  ") as call_llm,
        ):
            result = generate_review_package(
                _email(body_text="Please update this.\n\n> quoted history"),
                model="test-model",
                previous_draft="Previous draft",
                edit_version=2,
            )

        self.assertEqual(result["draft"], "Edited draft")
        self.assertEqual(result["version"], 2)
        route_email.assert_not_called()
        retrieve.assert_not_called()
        p1_prompt_path.read_text.assert_not_called()
        p2_prompt_path.read_text.assert_called_once_with(encoding="utf-8")
        call_llm.assert_called_once_with(
            model="test-model",
            system_prompt="P2 prompt",
            user_text=(
                "<PREVIOUS_DRAFT>\n"
                "Previous draft\n"
                "</PREVIOUS_DRAFT>\n\n"
                "<REVIEWER_REPLY_TEXT>\n"
                "Please update this.\n\n> quoted history\n"
                "</REVIEWER_REPLY_TEXT>\n\n"
                "Return the complete revised Ali response only."
            ),
            file_path=None,
        )

    def test_empty_previous_draft_still_selects_v2_path(self) -> None:
        p1_prompt_path = MagicMock()
        p2_prompt_path = MagicMock()
        p2_prompt_path.read_text.return_value = "Edit prompt"
        with (
            patch("ali.ali_llm.route_email") as route_email,
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            patch("ali.ali_llm.call_llm", return_value="Edited"),
        ):
            generate_review_package(
                _email(body_text="Reviewer note"),
                model="test-model",
                previous_draft="",
                edit_version=2,
            )

        route_email.assert_not_called()
        p1_prompt_path.read_text.assert_not_called()
        p2_prompt_path.read_text.assert_called_once_with(encoding="utf-8")

    def test_v2_missing_revision_prompt_raises(self) -> None:
        p1_prompt_path = MagicMock()
        p2_prompt_path = MagicMock()
        p2_prompt_path.read_text.side_effect = FileNotFoundError("Prompt file not found")
        with (
            patch("ali.ali_llm.P1_SYSTEM_PROMPT_PATH", p1_prompt_path),
            patch("ali.ali_llm.P2_REVISION_PROMPT_PATH", p2_prompt_path),
            patch("ali.ali_llm.route_email") as route_email,
            patch("ali.ali_llm.rag_retrieval") as retrieve,
            self.assertRaisesRegex(FileNotFoundError, "Prompt file not found"),
        ):
            generate_review_package(
                _email(),
                model="test-model",
                previous_draft="Previous draft",
                edit_version=2,
            )

        route_email.assert_not_called()
        retrieve.assert_not_called()
        p1_prompt_path.read_text.assert_not_called()
        p2_prompt_path.read_text.assert_called_once_with(encoding="utf-8")


class LlmReviewAndRenderingTests(unittest.TestCase):
    target_file = "ali_llm.py"

    def test_step4_disabled_is_noop(self) -> None:
        self.assertEqual(step4_review("Draft", enabled=False), "Draft")

    def test_step4_enabled_is_currently_noop(self) -> None:
        self.assertEqual(step4_review("Draft", enabled=True), "Draft")

    def test_render_review_wraps_draft_with_protocol(self) -> None:
        rendered = render_review({"draft": "Draft body", "version": 4})

        self.assertEqual(
            rendered,
            "=================   ALI'S RESPONSE - VERSION 4   ==================\n"
            "Draft body\n"
            "====================   ALI'S RESPONSE ENDED   =====================",
        )


class EmailTimingTests(unittest.TestCase):
    target_file = "ali_email.py"

    def test_default_poll_interval_is_shorter_during_hong_kong_business_hours(self) -> None:
        self.assertEqual(_default_poll_interval_minutes(datetime(2026, 1, 1, 9, 0)), 1)
        self.assertEqual(_default_poll_interval_minutes(datetime(2026, 1, 1, 17, 59)), 1)

    def test_default_poll_interval_is_longer_outside_business_hours(self) -> None:
        self.assertEqual(_default_poll_interval_minutes(datetime(2026, 1, 1, 8, 59)), 2)
        self.assertEqual(_default_poll_interval_minutes(datetime(2026, 1, 1, 18, 0)), 2)


class EmailGuardedExecutionTests(unittest.TestCase):
    target_file = "ali_email.py"

    def test_run_guarded_quarantines_deterministic_uid_failure(self) -> None:
        logger = MagicMock()

        def fail() -> None:
            raise FileNotFoundError("missing prompt")

        with patch("ali.ali_email._move_imap_message_to_failed") as move_failed:
            _run_guarded(logger=logger, ctx="ctx", uid=7, subject="Question", fn=fail)

        move_failed.assert_called_once_with(7, logger=logger)
        logger.exception.assert_called_once()
        logger.error.assert_called_once()

    def test_run_guarded_leaves_transient_failure_unseen_for_retry(self) -> None:
        logger = MagicMock()

        def fail() -> None:
            raise RuntimeError("offline")

        with patch("ali.ali_email._move_imap_message_to_failed") as move_failed:
            _run_guarded(logger=logger, ctx="ctx", uid=7, subject="Question", fn=fail)

        move_failed.assert_not_called()
        logger.exception.assert_called_once()

    def test_move_imap_message_to_failed_disconnects_client(self) -> None:
        cfg = _imap_config(folder="Inbox")
        client = MagicMock()
        with (
            patch("ali.ali_email.load_imap_config", return_value=cfg),
            patch("ali.ali_email.ImapClient", return_value=client) as client_cls,
        ):
            _move_imap_message_to_failed(7, logger=MagicMock())

        client_cls.assert_called_once_with(
            server=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            verify_ssl=cfg.verify_ssl,
            timeout=cfg.timeout,
            logger=unittest.mock.ANY,
        )
        client.connect.assert_called_once_with()
        client.move_message.assert_called_once_with("Inbox", 7, "Ali_failed")
        client.disconnect.assert_called_once_with()

    def test_move_imap_message_to_failed_disconnects_after_move_error(self) -> None:
        client = MagicMock()
        client.move_message.side_effect = RuntimeError("move failed")
        with (
            patch("ali.ali_email.load_imap_config", return_value=_imap_config()),
            patch("ali.ali_email.ImapClient", return_value=client),
            self.assertRaisesRegex(RuntimeError, "move failed"),
        ):
            _move_imap_message_to_failed(7, logger=MagicMock())

        client.disconnect.assert_called_once_with()


class EmailReviewPackagingTests(unittest.TestCase):
    target_file = "ali_email.py"

    def test_review_subject_strips_reply_prefix_and_replaces_old_marker(self) -> None:
        self.assertEqual(
            _build_review_subject(" Re:  Customer question [ALI:v1] ", 3),
            "Customer question [ALI:v3]",
        )

    def test_empty_review_subject_becomes_marker_only(self) -> None:
        self.assertEqual(_build_review_subject("", 2), "[ALI:v2]")

    def test_send_internal_review_self_addresses_reviewer(self) -> None:
        original = _email(from_addr="reviewer@example.com", subject="Customer question")
        logger = MagicMock()
        with patch("ali.ali_email.send_reply", return_value=SendResult(ok=True)) as send:
            _send_internal_review(original, "Review body", logger=logger, review_version=2)

        sent_msg, sent_body = send.call_args.args
        self.assertEqual(sent_body, "Review body")
        self.assertEqual(sent_msg.from_addr, "reviewer@example.com")
        self.assertEqual(sent_msg.to_addrs, ["reviewer@example.com"])
        self.assertEqual(sent_msg.subject, "Customer question [ALI:v2]")
        logger.info.assert_called_once()

    def test_send_internal_review_rejects_missing_reviewer_before_send(self) -> None:
        with (
            patch("ali.ali_email.send_reply") as send,
            self.assertRaisesRegex(RuntimeError, "Missing reviewer"),
        ):
            _send_internal_review(_email(from_addr=""), "Review body", logger=MagicMock())

        send.assert_not_called()

    def test_send_internal_review_raises_on_send_failure(self) -> None:
        with (
            patch(
                "ali.ali_email.send_reply",
                return_value=SendResult(ok=False, error_message="smtp down"),
            ),
            self.assertRaisesRegex(RuntimeError, "smtp down"),
        ):
            _send_internal_review(_email(), "Review body", logger=MagicMock())


class EmailPhaseOneTests(unittest.TestCase):
    target_file = "ali_email.py"

    def test_phase1_logs_when_no_new_messages(self) -> None:
        logger = MagicMock()
        with patch("ali.ali_email.fetch_new_messages", return_value=[]):
            _phase1_new_messages(logger=logger)

        logger.info.assert_called_once_with("No new messages to process.")

    def test_phase1_generates_sends_and_marks_new_message_seen(self) -> None:
        msg = _email(uid=11, subject="Customer question")
        logger = MagicMock()
        with (
            patch("ali.ali_email.fetch_new_messages", return_value=[msg]) as fetch,
            patch("ali.ali_email.generate_review_package", return_value={"draft": "Draft"}) as generate,
            patch("ali.ali_email.render_review", return_value="Rendered review") as render,
            patch("ali.ali_email._send_internal_review") as send_review,
            patch("ali.ali_email.mark_imap_message_seen") as mark_seen,
        ):
            _phase1_new_messages(logger=logger)

        fetch.assert_called_once_with(max_messages=2)
        generate.assert_called_once_with(
            msg,
            model=ali_email.LLM_MODEL,
        )
        render.assert_called_once_with({"draft": "Draft"})
        send_review.assert_called_once_with(msg, "Rendered review", logger=logger)
        mark_seen.assert_called_once_with(11, logger=logger)

    def test_phase1_skips_review_thread_without_generation_or_seen_mark(self) -> None:
        msg = _email(uid=11, subject="Customer question [ALI:v1]")
        with (
            patch("ali.ali_email.fetch_new_messages", return_value=[msg]),
            patch("ali.ali_email.generate_review_package") as generate,
            patch("ali.ali_email._send_internal_review") as send_review,
            patch("ali.ali_email.mark_imap_message_seen") as mark_seen,
        ):
            _phase1_new_messages(logger=MagicMock())

        generate.assert_not_called()
        send_review.assert_not_called()
        mark_seen.assert_not_called()


class EmailPhaseTwoTests(unittest.TestCase):
    target_file = "ali_email.py"

    def test_phase2_empty_reviewer_reply_marks_seen_without_generation(self) -> None:
        msg = _email(uid=12, body_text="   ")
        with (
            patch("ali.ali_email.fetch_sender_replies", return_value=[msg]),
            patch("ali.ali_email.generate_review_package") as generate,
            patch("ali.ali_email._send_internal_review") as send_review,
            patch("ali.ali_email.mark_imap_message_seen") as mark_seen,
        ):
            _phase2_sender_replies(logger=MagicMock())

        generate.assert_not_called()
        send_review.assert_not_called()
        mark_seen.assert_called_once_with(12, logger=unittest.mock.ANY)

    def test_phase2_edits_previous_draft_and_increments_version(self) -> None:
        msg = _email(uid=12, subject="Customer question [ALI:v2]", body_text="Please revise")
        logger = MagicMock()
        with (
            patch("ali.ali_email.fetch_sender_replies", return_value=[msg]),
            patch("ali.ali_email.extract_reviewer_reply_text", return_value="Please revise"),
            patch(
                "ali.ali_email.extract_last_review_state",
                return_value=ReviewState(version=2, draft="Previous draft"),
            ),
            patch("ali.ali_email.generate_review_package", return_value={"draft": "Edited"}) as generate,
            patch("ali.ali_email.render_review", return_value="Rendered edit") as render,
            patch("ali.ali_email._send_internal_review") as send_review,
            patch("ali.ali_email.mark_imap_message_seen") as mark_seen,
        ):
            _phase2_sender_replies(logger=logger)

        reviewer_input = generate.call_args.args[0]
        self.assertEqual(reviewer_input.body_text, "Please revise")
        generate.assert_called_once_with(
            reviewer_input,
            model=ali_email.LLM_MODEL,
            previous_draft="Previous draft",
            edit_version=3,
        )
        render.assert_called_once_with({"draft": "Edited"})
        send_review.assert_called_once_with(
            msg,
            "Rendered edit",
            logger=logger,
            base_subject=msg.subject,
            review_version=3,
        )
        mark_seen.assert_called_once_with(12, logger=logger)


class EmailPipelineTests(unittest.TestCase):
    target_file = "ali_email.py"

    def test_main_loop_configures_logger_and_runs_both_phases(self) -> None:
        class StopLoop(Exception):
            pass

        logger = MagicMock()
        with (
            patch("helper.helper_config.configure_logging", return_value=logger) as configure,
            patch("ali.ali_fetch.fetch_new_messages", return_value=[]) as fetch_new,
            patch("ali.ali_fetch.fetch_sender_replies", return_value=[]) as fetch_replies,
            patch("helper.helper_config.get_env_int", return_value=1) as get_interval,
            patch("time.sleep", side_effect=StopLoop) as sleep,
        ):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="'ali\\.ali_email' found in sys\\.modules.*", category=RuntimeWarning)
                with self.assertRaises(StopLoop):
                    runpy.run_module("ali.ali_email", run_name="__main__")

        configure.assert_called_once_with("ali_pipeline")
        fetch_new.assert_called_once_with(max_messages=2)
        fetch_replies.assert_called_once_with()
        get_interval.assert_called_once()
        self.assertEqual(get_interval.call_args.args[0], "ALI_POLL_INTERVAL_MINUTES")
        self.assertIn(get_interval.call_args.args[1], (1, 2))
        sleep.assert_called_once_with(60)
        logger.info.assert_any_call("Pipeline run finished.")


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


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    target_order = {
        "ali_parse.py": 0,
        "ali_fetch.py": 1,
        "ali_llm.py": 2,
        "ali_email.py": 3,
        "ali_send.py": 4,
    }
    test_classes = [
        obj
        for obj in globals().values()
        if isinstance(obj, type)
        and issubclass(obj, unittest.TestCase)
        and getattr(obj, "target_file", None)
    ]
    test_classes.sort(
        key=lambda cls: (
            target_order.get(cls.target_file, len(target_order)),
            cls.__name__,
        )
    )

    suite = unittest.TestSuite()
    for test_class in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(test_class))
    return suite


if __name__ == "__main__":
    unittest.main(testRunner=EmojiTextTestRunner, verbosity=2)
