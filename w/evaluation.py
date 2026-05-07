from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
W_DIR = Path(__file__).resolve().parent

for path in (ROOT_DIR, W_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from p import CONFIG  # noqa: E402
from p_pipelines import read_next_download_url, remove_download_url_line  # noqa: E402
from p_torrent import scan_torrent_watch_folder  # noqa: E402
from p_ttml import handle_ttml  # noqa: E402
from utils_text import sanitize_and_trim_filename  # noqa: E402
from utils_unlink import WikilinkCleaner  # noqa: E402


OK = "✅"
FAIL = "❌"


@dataclass(frozen=True)
class EvalPaths:
    watch: Path
    whisper: Path

    ttml_watch: Path

    pretext_watch: Path
    pretext_done: Path
    premium_watch: Path

    extract_watch: Path
    extract: Path

    original: Path
    archive: Path
    fail: Path

    audio_watch_folders: tuple[Path, ...]
    audio_done: Path
    audio_transcribed: Path

    obsidian: Path
    link_backup: Path

    x_list: Path
    download_target: Path

PATHS = EvalPaths(
    watch=Path(CONFIG["WATCH_FOLDER"]),
    whisper=Path(CONFIG["WHISPER_FOLDER"]),

    ttml_watch=Path(CONFIG["TTML_WATCH_FOLDER"]),

    pretext_watch=Path(CONFIG["PRETEXT_WATCH_FOLDER"]),
    pretext_done=Path(CONFIG["PRETEXT_DONE_FOLDER"]),
    premium_watch=Path(CONFIG["PREMIUM_WATCH_FOLDER"]),

    extract_watch=Path(CONFIG["EXTRACT_WATCH_FOLDER"]),
    extract=Path(CONFIG["EXTRACT_FOLDER"]),

    original=Path(CONFIG["ORIGINAL_FOLDER"]),
    archive=Path(CONFIG["ARCHIVE_FOLDER"]),
    fail=Path(CONFIG["FAIL_FOLDER"]),

    audio_watch_folders=tuple(Path(p) for p in CONFIG["AUDIO_WATCH_FOLDERS"]),
    audio_done=Path(CONFIG["AUDIO_DONE_FOLDER"]),
    audio_transcribed=Path(CONFIG["AUDIO_TRANSCRIBED_TXT_FOLDER"]),

    obsidian=Path(CONFIG["OBSIDIAN_SYNC_FOLDER"]),
    link_backup=Path(CONFIG["LINK_BACKUP_FOLDER"]),

    x_list=Path(CONFIG["X_URL_LIST_FILE"]),
    download_target=Path(CONFIG["DOWNLOAD_TARGET_FOLDER"]),
)


def safe_delete(path: Path, test_id: str) -> bool:
    if path.exists() and path.is_file() and test_id in path.name:
        path.unlink()
        return True
    return False


def print_result(name: str, passed: bool, details: dict) -> None:
    print(f"{OK if passed else FAIL} {name}")
    for key, value in details.items():
        print(f"  {key}: {value}")


def cleanup_files(paths: list[Path], test_id: str) -> None:
    deleted = 0
    leftover = 0
    seen: set[Path] = set()

    for path in paths:
        if path in seen:
            continue
        seen.add(path)

        safe_delete(path, test_id)

        if path.exists() and test_id in path.name:
            leftover += 1
        elif not path.exists():
            deleted += 1

    print(f"\n🧹 cleanup: {deleted} clean, {leftover} leftover")


def test_summary(results: list[bool]) -> None:
    passed = sum(1 for result in results if result)
    failed = len(results) - passed
    print(f"✅ summary: {passed} ✅, {failed} ❌, {len(results)} total")


def test_torrent_move(test_id: str) -> tuple[bool, list[Path]]:
    filename = f"{test_id}.torrent"
    source = PATHS.watch / filename
    target = PATHS.whisper / filename

    cleanup = [source, target]
    cleanup.append(target.with_suffix(".torrent.qbt_rejected"))

    PATHS.watch.mkdir(parents=True, exist_ok=True)
    PATHS.whisper.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy torrent test {test_id}\n", encoding="utf-8")
    time.sleep(2)

    moved_count = scan_torrent_watch_folder(CONFIG)

    passed = moved_count >= 1 and not source.exists() and target.is_file()

    print_result(
        "torrent move",
        passed,
        {
            "source": source,
            "target": target,
            "moved_count": moved_count,
        },
    )

    return passed, cleanup


def test_ttml_convert(test_id: str) -> tuple[bool, list[Path]]:
    filename = f"{test_id}.ttml"
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])

    source = PATHS.ttml_watch / filename
    output = PATHS.ttml_watch / f"{test_id}{pretext_suffix}"
    archived = PATHS.original / filename

    cleanup = [source, output, archived, source.with_suffix(".ttml.processing")]

    PATHS.ttml_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)

    source.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tt>
  <body>
    <p>Hello TTML Test</p>
  </body>
</tt>
""",
        encoding="utf-8",
    )

    handle_ttml(
        str(source),
        str(PATHS.ttml_watch),
        str(PATHS.original),
        sanitize_and_trim_filename,
        pretext_suffix,
    )

    passed = not source.exists() and output.is_file() and archived.is_file()

    print_result(
        "ttml convert",
        passed,
        {
            "source": source,
            "output": output,
            "archived": archived,
        },
    )

    return passed, cleanup


def test_x_url_list_remove_completed(test_id: str) -> tuple[bool, list[Path]]:
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=evaluation"
    next_url = "https://www.instagram.com/p/evaluation/"
    source = PATHS.download_target / f"{test_id}_urls.txt"

    cleanup = [source]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    source.write_text(
        f"\nnot a url\n{url}\n{next_url}\n",
        encoding="utf-8",
    )

    found_url, active_path = read_next_download_url(source, set())
    removed = remove_download_url_line(source, found_url)
    remaining = source.read_text(encoding="utf-8")

    passed = (
        found_url == url
        and active_path == source
        and removed
        and url not in remaining
        and "not a url" in remaining
        and next_url in remaining
    )

    print_result(
        "x url list remove completed",
        passed,
        {
            "source": source,
            "found_url": found_url,
            "removed": removed,
        },
    )

    return passed, cleanup


def test_wikilink_cleaner_removes_broken_link(test_id: str) -> tuple[bool, list[Path]]:
    valid_name = f"{test_id}_valid"
    source = PATHS.obsidian / f"W {test_id}.md"
    valid_note = PATHS.obsidian / f"{valid_name}.md"

    cleanup = [source, valid_note]

    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    valid_note.write_text(f"valid note for {test_id}\n", encoding="utf-8")
    source.write_text(
        f"Keep [[{valid_name}]]\nRemove [[{test_id}_missing]] please\n",
        encoding="utf-8",
    )

    cleaner = WikilinkCleaner(str(PATHS.obsidian), create_backup=False)
    processed = cleaner.process_file(source)
    updated = source.read_text(encoding="utf-8")
    stats = cleaner.get_stats()

    passed = (
        processed
        and f"[[{valid_name}]]" in updated
        and f"[[{test_id}_missing]]" not in updated
        and stats["files_processed"] == 1
        and stats["broken_links_removed"] == 1
        and stats["files_modified"] == 1
    )

    print_result(
        "wikilink cleaner removes broken link",
        passed,
        {
            "source": source,
            "valid_note": valid_note,
            "stats": stats,
        },
    )

    return passed, cleanup


def main() -> int:
    test_id = f"EVAL_{uuid.uuid4().hex[:8]}"
    all_cleanup: list[Path] = []
    results: list[bool] = []

    try:
        for test in (
            test_torrent_move,
            test_ttml_convert,
            test_x_url_list_remove_completed,
            test_wikilink_cleaner_removes_broken_link,
        ):
            passed, cleanup = test(test_id)
            results.append(passed)
            all_cleanup.extend(cleanup)

        return 0 if all(results) else 1

    except Exception as exc:
        print(f"{FAIL} evaluation")
        print(f"  error: {exc}")
        return 1

    finally:
        cleanup_files(all_cleanup, test_id)
        test_summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
