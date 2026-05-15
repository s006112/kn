"""
helper_text.py
Text utilities for filename sanitization and chunking/merging long strings.

Used by:
* w/p_audio.py
* w/p_txt.py

Pipelines:
- raw_name -> unicode_normalize -> char_filter -> whitespace_collapse -> safe_name
- safe_name -> length_check -> trim -> safe_name
- text -> chunking -> overlaps -> chunks
- chunks -> lcs_overlap -> merge -> text

"""

import logging
import re
import unicodedata
import os

# Avoid Windows device/reserved names that would make file creation fail.
_RESERVED_WINDOWS_NAMES = {
	"con",
	"prn",
	"aux",
	"nul",
	*{f"com{i}" for i in range(1, 10)},
	*{f"lpt{i}" for i in range(1, 10)},
}
# Keep generated names compatible across platforms and avoid control characters.
_INVALID_FILENAME_CHARS = set("#[]`/\\?*<>|：:｜")
_CONTROL_CHAR_PATTERN = re.compile(r"[\u0000-\u001f\u007f]")
_REPLACEMENT_CHAR = "・"

def short_log_name(name, keep=10):
    name = os.path.basename(os.fspath(name))
    stem, ext = os.path.splitext(name)
    suffix = f"_p{ext}" if stem.lower().endswith("_p") else ext
    stem = stem[:-2] if suffix.lower().startswith("_p") else stem
    return name if len(stem) <= keep else f"{stem[:keep]}...{suffix}"

def _normalize_unicode_name(name: str) -> str:
	"""Normalize Unicode text to NFKC form."""
	return unicodedata.normalize("NFKC", name)


def sanitize_filename(name: str) -> str:
	"""Convert arbitrary text into a filesystem-friendly filename component."""
	normalized = _normalize_unicode_name(name)
	normalized = _CONTROL_CHAR_PATTERN.sub(" ", normalized)
	sanitized_chars = []
	for ch in normalized:
		category = unicodedata.category(ch)

		if ch in _INVALID_FILENAME_CHARS or ord(ch) > 0xFFFF:
			sanitized_chars.append(_REPLACEMENT_CHAR)
		elif category == "Cs":  # surrogate code units
			sanitized_chars.append(_REPLACEMENT_CHAR)
		elif category.startswith("C"):  # other non-printable/format characters
			sanitized_chars.append(" ")
		else:
			sanitized_chars.append(ch)

	sanitized = "".join(sanitized_chars)
	sanitized = re.sub(r"\s+", " ", sanitized).strip()
	sanitized = sanitized.rstrip(". ")

	if not sanitized:
		sanitized = "untitled"

	if sanitized.lower() in _RESERVED_WINDOWS_NAMES:
		sanitized = f"{sanitized}_"

	return sanitized


def sanitize_and_trim_filename(base_name: str, max_length: int = 50) -> str:
	"""Sanitize a filename component and trim it to `max_length` characters."""
	sanitized_name = sanitize_filename(base_name)
	try:
		if len(sanitized_name) > max_length:
			sanitized_name = sanitized_name[:max_length].rstrip(". ")
			if not sanitized_name:
				sanitized_name = "untitled"
		return sanitized_name
	except Exception as e:
		logging.error("Error trimming base name '%s': %s", base_name, e)
		return sanitized_name


def chunk_text(text: str, chunk_size: int = 2000, overlap: int = 20):
	"""Split text into chunks with fixed overlap between adjacent chunks."""
	text_length = len(text)
	n_chunks = (text_length + chunk_size - 1) // chunk_size
	if n_chunks <= 1:
		return [text]
	optimal_chunk_size = text_length // n_chunks
	if optimal_chunk_size < (chunk_size * 0.5):
		n_chunks = max(
			1, int((text_length + chunk_size * 0.5 - 1) // (chunk_size * 0.5))
		)
		optimal_chunk_size = text_length // n_chunks
	chunks = []
	start = 0
	for i in range(n_chunks):
		if i == n_chunks - 1:
			chunks.append(text[start:])
			break
		end = start + optimal_chunk_size
		if i < n_chunks - 1:
			end += overlap
		chunks.append(text[start:end])
		start = end - overlap
	return chunks


def intelligent_merge_chunks(chunks, window: int = 30, min_len: int = 4) -> str:
	"""Merge chunks by removing detected prefix and suffix overlaps."""
	if not chunks:
		return ""
	if len(chunks) == 1:
		return chunks[0]

	# 计算两个字符串的最长公共子串位置和长度
	def longest_common_substring(a: str, b: str):
		max_len = 0
		start_a = start_b = 0
		dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
		for i in range(1, len(a) + 1):
			for j in range(1, len(b) + 1):
				if a[i - 1] == b[j - 1]:
					dp[i][j] = dp[i - 1][j - 1] + 1
					if dp[i][j] > max_len:
						max_len = dp[i][j]
						start_a = i - max_len
						start_b = j - max_len
		return start_a, start_b, max_len

	merged = chunks[0]
	for i in range(1, len(chunks)):
		prev = merged[-window:] if len(merged) > window else merged
		curr = chunks[i][:window] if len(chunks[i]) > window else chunks[i]
		start_a, start_b, lcs_len = longest_common_substring(prev, curr)
		if lcs_len >= min_len:
			merged_pos = len(merged) - len(prev) + start_a
			curr_pos = start_b + lcs_len
			merged = (
				merged[:merged_pos]
				+ prev[start_a : start_a + lcs_len]
				+ chunks[i][curr_pos:]
			)
		else:
			merged += chunks[i]
	return merged
