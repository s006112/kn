from __future__ import annotations

import ast
import sys
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from queue import Queue


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import w.p as orchestrator_module
import w.helper_files as helper_files_module
import w.p_txt as txt_process_module
from w.p import CONFIG
pipelines = orchestrator_module

import w.p_audio as audio_module
from w.p_audio import move_files_to_done, scan_audio_files

from w.p_torrent import (
    move_torrent_to_whisper,
    scan_torrent_watch_folder,
)
import w.p_ytd as ytd_module
from w.p_ytd import (
    process_ytd_pipeline,
    read_next_download_url,
    remove_download_url_line,
)

from w.p_ttml import handle_ttml
from w.helper_md import merge_to_markdown
from w.helper_text import sanitize_and_trim_filename, sanitize_filename
from w.p_wiki import WikilinkCleaner, clean_dead_links
from w.helper_files import (
    get_next_available_filename,
    read_file_with_encodings,
    safe_rename,
    release_text_file_permissions,
    write_text_file,
)
from helper.helper_llm import LLMPermanentFailure


OK = "✅"
FAIL = "❌"
EXTRACT_INPUT_SUFFIX = "_p.txt"


@dataclass(frozen=True)
class EvalPaths:
    watch: Path
    whisper: Path

    ttml_watch: Path

    pretext_watch: Path
    pretext_done: Path

    extract_watch: Path
    extract: Path

    original: Path

    audio_watch_folders: tuple[Path, ...]
    audio_done: Path
    audio_transcribed: Path

    obsidian: Path
    link_backup: Path

    ytd_list: Path
    download_target: Path

PATHS = EvalPaths(
    watch=Path(CONFIG["WATCH_FOLDER"]),
    whisper=Path(CONFIG["WHISPER_FOLDER"]),

    ttml_watch=Path(CONFIG["TTML_WATCH_FOLDER"]),

    pretext_watch=Path(CONFIG["PRETEXT_WATCH_FOLDER"]),
    pretext_done=Path(CONFIG["PRETEXT_DONE_FOLDER"]),

    extract_watch=Path(CONFIG["EXTRACT_WATCH_FOLDER"]),
    extract=Path(CONFIG["EXTRACT_DONE_FOLDER"]),

    original=Path(CONFIG["RAW_ARCHIVE_FOLDER"]),

    audio_watch_folders=tuple(Path(p) for p in CONFIG["AUDIO_WATCH_FOLDERS"]),
    audio_done=Path(CONFIG["AUDIO_DONE_FOLDER"]),
    audio_transcribed=Path(CONFIG["AUDIO_TRANSCRIBED_TXT_FOLDER"]),

    obsidian=Path(CONFIG["OBSIDIAN_SYNC_FOLDER"]),
    link_backup=Path(CONFIG["LINK_BACKUP_FOLDER"]),

    ytd_list=Path(CONFIG["YTD_LIST_FILE"]),
    download_target=Path(CONFIG["DOWNLOAD_TARGET_FOLDER"]),
)


def extract_input_suffix(config=CONFIG) -> str:
    return str(config["EXTRACT_SUFFIX"])


def extract_text_config(**overrides):
    return {**CONFIG, "EXTRACT_SUFFIX": EXTRACT_INPUT_SUFFIX, **overrides}


def safe_delete(path: Path, test_id: str) -> bool:
    if path.exists() and test_id in path.name:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.is_file():
            path.unlink()
        else:
            return False
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
    print(f"✅ {len(results)} TESTS : ✅ {passed} ; ❌ {failed}")


def test_torrent_move(test_id: str) -> tuple[bool, list[Path]]:
    watch_dir = ROOT_DIR / f"{test_id}_torrent_move_watch"
    whisper_dir = ROOT_DIR / f"{test_id}_torrent_move_whisper"
    filename = f"{test_id}.torrent"
    source = watch_dir / filename
    target = whisper_dir / filename

    cleanup = [watch_dir, whisper_dir]

    watch_dir.mkdir(parents=True, exist_ok=True)
    whisper_dir.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy torrent test {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)

    config = {
        **CONFIG,
        "WATCH_FOLDER": watch_dir,
        "WHISPER_FOLDER": whisper_dir,
    }

    moved_count = scan_torrent_watch_folder(config)

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


def test_torrent_move_avoids_overwrite(test_id: str) -> tuple[bool, list[Path]]:
    watch_dir = ROOT_DIR / f"{test_id}_torrent_duplicate_watch"
    whisper_dir = ROOT_DIR / f"{test_id}_torrent_duplicate_whisper"
    filename = f"{test_id}_duplicate.torrent"
    source = watch_dir / filename
    existing_target = whisper_dir / filename
    collision_target = whisper_dir / f"{test_id}_duplicate_1.torrent"

    cleanup = [watch_dir, whisper_dir]

    watch_dir.mkdir(parents=True, exist_ok=True)
    whisper_dir.mkdir(parents=True, exist_ok=True)

    source.write_text(f"new torrent source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)
    existing_target.write_text(f"existing torrent target {test_id}\n", encoding="utf-8")
    release_text_file_permissions(existing_target)

    moved = move_torrent_to_whisper(str(source), str(whisper_dir))

    existing_content = existing_target.read_text(encoding="utf-8")
    collision_content = (
        collision_target.read_text(encoding="utf-8")
        if collision_target.exists()
        else ""
    )

    passed = (
        moved
        and not source.exists()
        and existing_target.is_file()
        and collision_target.is_file()
        and f"existing torrent target {test_id}" in existing_content
        and f"new torrent source {test_id}" in collision_content
    )

    print_result(
        "torrent move avoids overwrite",
        passed,
        {
            "source": source,
            "existing_target": existing_target,
            "collision_target": collision_target,
            "moved": moved,
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

def test_ttml_plain_text_branch_converts_and_archives(test_id: str) -> tuple[bool, list[Path]]:
    filename = f"{test_id}_plain.ttml"
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])

    source = PATHS.ttml_watch / filename
    output = PATHS.ttml_watch / f"{test_id}_plain{pretext_suffix}"
    archived = PATHS.original / filename

    content = f"""plain subtitle line one {test_id}
plain subtitle line two {test_id}
"""

    cleanup = [source, output, archived, source.with_suffix(".ttml.processing")]

    PATHS.ttml_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)

    source.write_text(content, encoding="utf-8")
    release_text_file_permissions(source)

    handle_ttml(
        str(source),
        str(PATHS.ttml_watch),
        str(PATHS.original),
        sanitize_and_trim_filename,
        pretext_suffix,
    )

    output_text = output.read_text(encoding="utf-8") if output.exists() else ""

    passed = (
        not source.exists()
        and output.is_file()
        and archived.is_file()
        and output_text == content
    )

    print_result(
        "ttml plain text branch converts and archives",
        passed,
        {
            "source": source,
            "output": output,
            "archived": archived,
        },
    )

    return passed, cleanup

def test_ytd_list_remove_completed(test_id: str) -> tuple[bool, list[Path]]:
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
        "ytd list remove completed",
        passed,
        {
            "source": source,
            "found_url": found_url,
            "removed": removed,
        },
    )

    return passed, cleanup

def test_ytd_pipeline_mocked_loop_removes_completed_url(test_id: str) -> tuple[bool, list[Path]]:
    url = f"https://www.youtube.com/watch?v={test_id}"
    list_file = PATHS.download_target / f"{test_id}_ytd_urls.txt"
    output = PATHS.download_target / f"{test_id}_downloaded.mp4"

    cleanup = [list_file, output]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    list_file.write_text(f"\nnot a url\n{url}\n", encoding="utf-8")
    release_text_file_permissions(list_file)

    config = {
        **CONFIG,
        "YTD_LIST_FILE": list_file,
        "DOWNLOAD_TARGET_FOLDER": PATHS.download_target,
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "SCAN_SECONDS": 0.05,
            "YTD_RESOLVE_TIMEOUT_SECONDS": 0.05,
        },
    }

    shutdown_flag = threading.Event()
    original_download = ytd_module.download

    try:
        def fake_download(
            _url: str,
            _quality: str,
            *,
            output_dir: Path,
            resolve_timeout: float,
        ) -> tuple[str, None]:
            output.write_text(f"fake download for {test_id}\n", encoding="utf-8")
            release_text_file_permissions(output)
            return str(output), None

        ytd_module.download = fake_download

        thread = threading.Thread(
            target=process_ytd_pipeline,
            args=(config, shutdown_flag),
            daemon=True,
        )
        thread.start()

        deadline = time.time() + 2
        while time.time() < deadline:
            remaining = list_file.read_text(encoding="utf-8") if list_file.exists() else ""
            if output.exists() and url not in remaining:
                break
            time.sleep(0.05)

        shutdown_flag.set()
        thread.join(timeout=1)

        remaining = list_file.read_text(encoding="utf-8") if list_file.exists() else ""

        passed = (
            output.is_file()
            and url not in remaining
            and "not a url" in remaining
            and not thread.is_alive()
        )

        print_result(
            "ytd pipeline mocked loop removes completed url",
            passed,
            {
                "list_file": list_file,
                "output": output,
                "url_removed": url not in remaining,
                "thread_alive": thread.is_alive(),
            },
        )

        return passed, cleanup

    finally:
        ytd_module.download = original_download
        shutdown_flag.set()

def test_ytd_uses_shared_write_text_file_static(test_id: str) -> tuple[bool, list[Path]]:
    ytd_path = ROOT_DIR / "w" / "p_ytd.py"
    ytd_source = ytd_path.read_text(encoding="utf-8")
    ytd_tree = ast.parse(ytd_source)
    functions = {
        node.name: node
        for node in ytd_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    remove_helper = functions.get("remove_download_url_line")
    download_helper = functions.get("download_ytd_url")
    pipeline_helper = functions.get("process_ytd_pipeline")
    imports_write_text_file = any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"helper_files", "w.helper_files"}
        and any(alias.name == "write_text_file" for alias in node.names)
        for node in ytd_tree.body
    )

    def calls_name(function_node: ast.FunctionDef | None, name: str) -> bool:
        return bool(
            function_node
            and any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
                for node in ast.walk(function_node)
            )
        )

    def nodes_call_name(nodes: list[ast.stmt], name: str) -> bool:
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
            for statement in nodes
            for node in ast.walk(statement)
        )

    remove_calls_write_text_file = calls_name(remove_helper, "write_text_file")
    remove_calls_path_write_text = bool(
        remove_helper
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
            for node in ast.walk(remove_helper)
        )
    )
    remove_preserves_newline = bool(
        remove_helper
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "write_text_file"
            and any(
                keyword.arg == "newline"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == ""
                for keyword in node.keywords
            )
            for node in ast.walk(remove_helper)
        )
    )
    download_has_try = bool(download_helper and any(isinstance(node, ast.Try) for node in ast.walk(download_helper)))
    pipeline_download_try_logs_failure = bool(
        pipeline_helper
        and any(
            isinstance(node, ast.Try)
            and nodes_call_name(node.body, "download_ytd_url")
            and "YTDPipeline: Download failed" in (ast.get_source_segment(ytd_source, node) or "")
            for node in ast.walk(pipeline_helper)
        )
    )

    passed = (
        imports_write_text_file
        and remove_calls_write_text_file
        and not remove_calls_path_write_text
        and remove_preserves_newline
        and not download_has_try
        and pipeline_download_try_logs_failure
    )

    print_result(
        "ytd uses shared write text file static",
        passed,
        {
            "imports_write_text_file": imports_write_text_file,
            "remove_calls_write_text_file": remove_calls_write_text_file,
            "remove_calls_path_write_text": remove_calls_path_write_text,
            "remove_preserves_newline": remove_preserves_newline,
            "download_has_try": download_has_try,
            "pipeline_download_try_logs_failure": pipeline_download_try_logs_failure,
        },
    )

    return passed, []

def test_ytd_remove_uses_shared_write_text_file_contract(test_id: str) -> tuple[bool, list[Path]]:
    first_url = f"https://www.youtube.com/watch?v={test_id}one"
    next_url = f"https://youtu.be/{test_id}two"
    source = PATHS.download_target / f"{test_id}_ytd_shared_write_urls.txt"
    cleanup = [source]
    captured = {}

    PATHS.download_target.mkdir(parents=True, exist_ok=True)
    source.write_text(f"\ninvalid text\n{first_url}\n{next_url}\n", encoding="utf-8")

    original_write_text_file = ytd_module.write_text_file

    try:
        def fake_write_text_file(path, content, *, newline=None):
            captured.update({"path": path, "content": content, "newline": newline})
            return path

        ytd_module.write_text_file = fake_write_text_file
        removed = remove_download_url_line(source, first_url)
        written_content = captured.get("content", "")

        passed = (
            removed
            and captured.get("path") == source
            and captured.get("newline") == ""
            and first_url not in written_content
            and "invalid text" in written_content
            and next_url in written_content
        )

        print_result(
            "ytd remove uses shared write text file contract",
            passed,
            {
                "source": source,
                "removed": removed,
                "captured": captured,
            },
        )

        return passed, cleanup

    finally:
        ytd_module.write_text_file = original_write_text_file

def test_wikilink_cleaner_removes_broken_link(test_id: str) -> tuple[bool, list[Path]]:
    valid_name = f"{test_id}_valid"
    source = PATHS.obsidian / f"W {test_id}.md"
    valid_note = PATHS.obsidian / f"{valid_name}.md"

    cleanup = [source, valid_note]

    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    valid_note.write_text(f"valid note for {test_id}\n", encoding="utf-8")
    release_text_file_permissions(valid_note)
    source.write_text(
        f"Keep [[{valid_name}]]\nRemove [[{test_id}_missing]] please\n",
        encoding="utf-8",
    )
    release_text_file_permissions(source)

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


def test_markdown_merge_updates_index(test_id: str) -> tuple[bool, list[Path]]:
    note = PATHS.download_target / f"{test_id}_note.md"
    whisper_index = PATHS.download_target / f"{test_id}_Whisper_000000.md"

    cleanup = [note, whisper_index]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    note.write_text(f"# Existing\n\nbody for {test_id}\n", encoding="utf-8")
    release_text_file_permissions(note)
    whisper_index.write_text("# Whisper\n---\n", encoding="utf-8")
    release_text_file_permissions(whisper_index)

    merge_to_markdown(
        str(note),
        [f"extracted result for {test_id}"],
        "",
        ["evaluation-model"],
        str(whisper_index),
        note.stem,
        True,
    )

    updated_note = note.read_text(encoding="utf-8")
    updated_index = whisper_index.read_text(encoding="utf-8")
    link = f"[[{note.stem}]]"

    passed = (
        note.is_file()
        and whisper_index.is_file()
        and updated_note.startswith("# evaluation-model\n\n")
        and f"extracted result for {test_id}" in updated_note
        and f"body for {test_id}" in updated_note
        and link in updated_index
        and updated_index.index(link) > updated_index.index("---")
    )

    print_result(
        "markdown merge updates index",
        passed,
        {
            "note": note,
            "whisper_index": whisper_index,
            "link": link,
        },
    )

    return passed, cleanup


def test_helper_md_uses_shared_write_text_file_static(test_id: str) -> tuple[bool, list[Path]]:
    helper_md_path = ROOT_DIR / "w" / "helper_md.py"
    helper_md_source = helper_md_path.read_text(encoding="utf-8")
    helper_md_tree = ast.parse(helper_md_source)
    functions = {
        node.name: node
        for node in helper_md_tree.body
        if isinstance(node, ast.FunctionDef)
    }

    imports_write_text_file = any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"helper_files", "w.helper_files"}
        and any(alias.name == "write_text_file" for alias in node.names)
        for node in helper_md_tree.body
    )
    imports_release_directly = any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"helper_files", "w.helper_files"}
        and any(alias.name == "release_text_file_permissions" for alias in node.names)
        for node in helper_md_tree.body
    )

    direct_write_release_lines = []
    lines = helper_md_source.splitlines()
    for index, line in enumerate(lines, 1):
        if (
            "with open(" in line
            and "encoding=" in line
            and ('"w"' in line or "'w'" in line)
        ):
            window = "\n".join(lines[index - 1 : index + 5])
            if "release_text_file_permissions(" in window:
                direct_write_release_lines.append(index)

    def function_calls(function_node: ast.FunctionDef | None, name: str) -> bool:
        return bool(
            function_node
            and any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
                for node in ast.walk(function_node)
            )
        )

    write_pretext_calls_write_text_file = function_calls(
        functions.get("write_pretext_markdown"), "write_text_file"
    )
    merge_calls_write_text_file = function_calls(
        functions.get("merge_to_markdown"), "write_text_file"
    )
    update_index_calls_write_text_file = function_calls(
        functions.get("update_whisper_index_for_pretext"), "write_text_file"
    )

    passed = (
        imports_write_text_file
        and not imports_release_directly
        and not direct_write_release_lines
        and write_pretext_calls_write_text_file
        and merge_calls_write_text_file
        and update_index_calls_write_text_file
    )

    print_result(
        "helper md uses shared write text file static",
        passed,
        {
            "imports_write_text_file": imports_write_text_file,
            "imports_release_directly": imports_release_directly,
            "direct_write_release_lines": direct_write_release_lines,
            "write_pretext_calls_write_text_file": write_pretext_calls_write_text_file,
            "merge_calls_write_text_file": merge_calls_write_text_file,
            "update_index_calls_write_text_file": update_index_calls_write_text_file,
        },
    )

    return passed, []


def test_audio_move_to_done_removes_wav(test_id: str) -> tuple[bool, list[Path]]:
    source = PATHS.download_target / f"{test_id}_audio.mp3"
    wav = PATHS.download_target / f"{test_id}_audio.wav"
    target = PATHS.audio_done / source.name

    cleanup = [source, wav, target]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)
    PATHS.audio_done.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy audio source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)
    wav.write_text(f"dummy wav temp {test_id}\n", encoding="utf-8")
    release_text_file_permissions(wav)

    move_files_to_done(
        str(source),
        str(wav),
        0,
        str(PATHS.audio_done),
        source.name,
    )

    passed = not source.exists() and not wav.exists() and target.is_file()

    print_result(
        "audio move to done removes wav",
        passed,
        {
            "source": source,
            "wav": wav,
            "target": target,
        },
    )

    return passed, cleanup


def test_pretext_scan_deduplicates_queue(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = extract_input_suffix()
    premium_suffix = str(CONFIG["PREMIUM_SUFFIX"])
    watch_dir = ROOT_DIR / f"{test_id}_pretext_scan_watch"
    source = watch_dir / f"{test_id}_pretext{pretext_suffix}"
    excluded = watch_dir / f"{test_id}_already_extract{extract_suffix}"
    premium_excluded = watch_dir / f"{test_id}_already_premium{premium_suffix}"
    symlink = watch_dir / "X.txt"

    cleanup = [source, excluded, premium_excluded, symlink, watch_dir]

    watch_dir.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy pretext queue source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)
    excluded.write_text(f"dummy pretext excluded source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(excluded)
    premium_excluded.write_text(f"dummy pretext premium excluded source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(premium_excluded)
    symlink.symlink_to(source)

    config = extract_text_config(PRETEXT_WATCH_FOLDER=str(watch_dir))
    pretext_queue = Queue()
    processed_files_global = set()
    processed_files_lock = threading.Lock()
    txt_process_module.scan_text_files(config["PRETEXT_WATCH_FOLDER"], pretext_queue, config["PRETEXT_SUFFIX"], (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"]), processed_files_global, processed_files_lock)
    txt_process_module.scan_text_files(config["PRETEXT_WATCH_FOLDER"], pretext_queue, config["PRETEXT_SUFFIX"], (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"]), processed_files_global, processed_files_lock)
    queued_paths = list(pretext_queue.queue)
    normalized = str(source.resolve())
    excluded_normalized = str(excluded.resolve())
    premium_excluded_normalized = str(premium_excluded.resolve())
    symlink_normalized = str(symlink.absolute())

    passed = (
        queued_paths == [normalized]
        and normalized in processed_files_global
        and excluded_normalized not in processed_files_global
        and premium_excluded_normalized not in processed_files_global
        and symlink_normalized not in processed_files_global
    )

    print_result(
        "pretext scan deduplicates queue",
        passed,
        {
            "source": source,
            "excluded": excluded,
            "queued_paths": queued_paths,
            "processed_files": sorted(processed_files_global),
        },
    )

    return passed, cleanup

def test_pretext_full_process_writes_pretext_markdown_and_archive(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = extract_input_suffix()
    base_name = f"{test_id}_pretext_full"

    source = PATHS.pretext_watch / f"{base_name}{pretext_suffix}"
    output = PATHS.pretext_watch / f"{base_name}{extract_suffix}"
    archived = PATHS.original / f"{base_name}.txt"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [source, output, archived]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy pretext source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)

    config = {
        **extract_text_config(),
        "PRETEXT_MODEL": "evaluation-model",
        "PRETEXT_PROMPT": "evaluation pretext prompt",
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "LLM_MAX_RETRIES": 4,
            "LLM_TIMEOUT_SECONDS": 44,
            "LLM_RETRY_DELAY_SECONDS": 6,
        },
    }
    expected_llm_options = {
        "max_retries": config["INTERVALS"]["LLM_MAX_RETRIES"],
        "timeout": config["INTERVALS"]["LLM_TIMEOUT_SECONDS"],
        "retry_delay": config["INTERVALS"]["LLM_RETRY_DELAY_SECONDS"],
    }

    original_call_llm = txt_process_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    captured_llm_options = []

    try:
        def fake_call_llm(**kwargs) -> str:
            captured_llm_options.append(
                {
                    "max_retries": kwargs.get("max_retries"),
                    "timeout": kwargs.get("timeout"),
                    "retry_delay": kwargs.get("retry_delay"),
                }
            )
            return f"mock pretext result {test_id}"

        txt_process_module.call_llm = fake_call_llm

        processed_files_global = set()
        processed_files_lock = threading.Lock()
        txt_process_module.process_pretext_file(
            config,
            str(source),
            processed_files_global,
            processed_files_lock,
        )

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        cleanup.extend(notes)

        output_text = output.read_text(encoding="utf-8") if output.exists() else ""
        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        passed = (
            not source.exists()
            and archived.is_file()
            and output.is_file()
            and f"mock pretext result {test_id}" in output_text
            and note is not None
            and f"mock pretext result {test_id}" in note_text
            and str(source.resolve()) not in processed_files_global
            and captured_llm_options
            and all(options == expected_llm_options for options in captured_llm_options)
        )

        print_result(
            "pretext full process writes pretext markdown and archive",
            passed,
            {
                "source": source,
                "output": output,
                "markdown": note,
                "archived": archived,
                "llm_options": captured_llm_options,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_pretext_partial_error_filename_and_content_unchanged(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = extract_input_suffix()
    base_name = f"{test_id}_pretext_partial_error"

    source = PATHS.pretext_watch / f"{base_name}{pretext_suffix}"
    error_file = PATHS.pretext_watch / f"{base_name}.error"
    output = PATHS.pretext_watch / f"{base_name}{extract_suffix}"
    archived = PATHS.original / f"{base_name}.txt"

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy pretext partial source {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)

    config = {
        **extract_text_config(),
        "PRETEXT_MODEL": "evaluation-model",
        "PRETEXT_PROMPT": "evaluation pretext prompt",
    }

    cleanup = [source, output, error_file, archived]

    original_call_llm = txt_process_module.call_llm

    try:
        failure_message = f"mock pretext llm failure {test_id}"

        def fail_call_llm(**_kwargs) -> str:
            raise RuntimeError(failure_message)

        txt_process_module.call_llm = fail_call_llm

        processed_files_global = {str(source.resolve())}
        processed_files_lock = threading.Lock()

        raised = False
        try:
            txt_process_module.process_pretext_file(
                config,
                str(source),
                processed_files_global,
                processed_files_lock,
            )
        except RuntimeError as exc:
            raised = str(exc) == failure_message

        passed = (
            raised
            and not source.exists()
            and error_file.is_file()
            and not archived.exists()
            and not output.exists()
            and str(source.resolve()) not in processed_files_global
        )

        print_result(
            "pretext failure renames source in place",
            passed,
            {
                "source": source,
                "output": output,
                "error_file": error_file,
                "raised": raised,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm


def test_distill_collects_extract_outputs(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    base_name = f"{test_id}_distill"
    first = PATHS.extract / f"{base_name}_model-alpha{pretext_suffix}"
    second = PATHS.extract / f"{base_name}_model-beta_1{pretext_suffix}"
    ignored = PATHS.extract / f"{test_id}_other_model{pretext_suffix}"

    cleanup = [first, second, ignored]

    PATHS.extract.mkdir(parents=True, exist_ok=True)

    first.write_text(f"alpha extract for {test_id}\n", encoding="utf-8")
    release_text_file_permissions(first)
    second.write_text(f"beta extract for {test_id}\n", encoding="utf-8")
    release_text_file_permissions(second)
    ignored.write_text(f"ignored extract for {test_id}\n", encoding="utf-8")
    release_text_file_permissions(ignored)

    extracts = txt_process_module.collect_extracts(str(PATHS.extract), base_name, pretext_suffix)
    filenames = [fname for fname, _, _ in extracts]
    contents = [content for _, content, _ in extracts]
    paths = [Path(path) for _, _, path in extracts]

    passed = (
        len(extracts) == 2
        and filenames == [first.name, second.name]
        and f"alpha extract for {test_id}" in contents[0]
        and f"beta extract for {test_id}" in contents[1]
        and paths == [first, second]
    )

    print_result(
        "distill collects extract outputs",
        passed,
        {
            "base_name": base_name,
            "filenames": filenames,
            "paths": paths,
        },
    )

    return passed, cleanup


def test_distillation_read_error_raises_without_sidecar(test_id: str) -> tuple[bool, list[Path]]:
    base_name = f"{test_id}_distill_read_error"
    read_message = f"mock distill read error {test_id}"

    PATHS.watch.mkdir(parents=True, exist_ok=True)

    config = {
        **CONFIG,
        "DISTILL_MODEL": "evaluation-distill-model",
        "DISTILL_PROMPT": "evaluation distill prompt",
    }
    error_file = PATHS.watch / f"{base_name}.{sanitize_filename(config['DISTILL_MODEL'])}.error"
    cleanup = [error_file]

    original_collect_extracts = txt_process_module.collect_extracts

    try:
        def fail_collect_extracts(*_args, **_kwargs):
            raise RuntimeError(read_message)

        txt_process_module.collect_extracts = fail_collect_extracts

        raised = False
        try:
            txt_process_module.run_distillation(config, base_name, md_path=None)
        except RuntimeError:
            raised = True

        passed = raised and not error_file.exists()

        print_result(
            "distillation read error raises without sidecar",
            passed,
            {
                "error_file": error_file,
                "raised": raised,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.collect_extracts = original_collect_extracts


def test_extract_worker_scan_queues_candidate_once(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = extract_input_suffix()
    premium_suffix = str(CONFIG["PREMIUM_SUFFIX"])
    watch_dir = ROOT_DIR / f"{test_id}_extract_scan_watch"
    source = watch_dir / f"{test_id}_extract{extract_suffix}"
    premium_source = watch_dir / f"{test_id}_premium{premium_suffix}"
    long_base = f"{test_id}_extract_" + "x" * 70
    long_source = watch_dir / f"{long_base}{extract_suffix}"
    long_renamed = watch_dir / f"{sanitize_and_trim_filename(long_base)}{extract_suffix}"
    ignored = PATHS.download_target / f"{test_id}_ignored{extract_suffix}"
    markdown = watch_dir / f"{test_id}_extract.md"
    error_file = watch_dir / f"{test_id}_extract.error"

    cleanup = [source, premium_source, long_source, long_renamed, ignored, markdown, error_file, watch_dir]

    watch_dir.mkdir(parents=True, exist_ok=True)
    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    source.write_text(f"extract queue candidate {test_id}\n", encoding="utf-8")
    release_text_file_permissions(source)
    premium_source.write_text(f"premium extract queue candidate {test_id}\n", encoding="utf-8")
    release_text_file_permissions(premium_source)
    long_source.write_text(f"extract long queue candidate {test_id}\n", encoding="utf-8")
    release_text_file_permissions(long_source)
    ignored.write_text(f"wrong folder candidate {test_id}\n", encoding="utf-8")
    release_text_file_permissions(ignored)
    markdown.write_text(f"markdown is not extract input {test_id}\n", encoding="utf-8")
    release_text_file_permissions(markdown)
    error_file.write_text(f"error marker is not extract input {test_id}\n", encoding="utf-8")
    release_text_file_permissions(error_file)

    config = extract_text_config(EXTRACT_WATCH_FOLDER=str(watch_dir))
    extract_queue = Queue()
    txt_process_module.scan_text_files(config["EXTRACT_WATCH_FOLDER"], extract_queue, (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"]))
    txt_process_module.scan_text_files(config["EXTRACT_WATCH_FOLDER"], extract_queue, (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"]))

    queued_paths = list(extract_queue.queue)
    source_normalized = str(source.resolve())
    premium_source_normalized = str(premium_source.resolve())
    long_renamed_normalized = str(long_renamed.resolve())

    passed = (
        queued_paths.count(source_normalized) == 1
        and queued_paths.count(premium_source_normalized) == 1
        and queued_paths.count(long_renamed_normalized) == 1
        and not long_source.exists()
        and long_renamed.is_file()
        and str(ignored.resolve()) not in queued_paths
        and str(markdown.resolve()) not in queued_paths
        and str(error_file.resolve()) not in queued_paths
    )

    print_result(
        "extract worker scan queues candidate once",
        passed,
        {
            "source": source,
            "long_source": long_source,
            "long_renamed": long_renamed,
            "ignored": ignored,
            "markdown": markdown,
            "error_file": error_file,
            "queued_paths": queued_paths,
        },
    )

    return passed, cleanup


def test_text_process_module_function_boundary(test_id: str) -> tuple[bool, list[Path]]:
    text_process_path = ROOT_DIR / "w" / "p_txt.py"
    outdated_text_process_path = ROOT_DIR / "w" / ("p_txt" + "_processing.py")
    removed_pretext_path = ROOT_DIR / "w" / ("p_" + "pretext.py")
    removed_extract_path = ROOT_DIR / "w" / ("p_" + "extract.py")
    removed_distill_path = ROOT_DIR / "w" / ("p_" + "distill.py")
    outdated_entrypoint = "start_" + "text_processing"
    outdated_module_name = "p_txt" + "_processing"
    removed_import_markers = {
        "w." + "p_" + "pretext",
        "w." + "p_" + "extract",
        "w." + "p_" + "distill",
        ".p_" + "pretext",
        ".p_" + "extract",
        ".p_" + "distill",
    }
    removed_names = {
        "BaseExtractProcessor",
        "ExtractProcessor",
        "PremiumExtractProcessor",
        "create_extract_processors",
    }
    required_names = {
        "process_pretext_file",
        "scan_text_files",
        "process_extract_file",
        "save_pipeline_error",
        "process_queue",
        "process_text_pipeline",
        "call_text_llm",
        "run_distillation",
        "collect_extracts",
        "save_extract_result",
    }
    forbidden_premium_markers = {
        "PREMIUM_EXTRACT",
        "PREMIUM_WATCH_FOLDER",
        "TextPipeline-PremiumExtract",
        "process_premium_extract_file",
        "scan_premium_extract_files",
        "premium_extract_queue",
        "process_premium_extract",
    }
    forbidden_p_import_names = required_names - {"process_text_pipeline"}
    exposed_removed = sorted(
        name for name in removed_names if hasattr(txt_process_module, name)
    )
    missing_required = sorted(
        name for name in required_names if not hasattr(txt_process_module, name)
    )
    p_source = (ROOT_DIR / "w" / "p.py").read_text(encoding="utf-8")
    txt_source = text_process_path.read_text(encoding="utf-8")
    txt_tree = ast.parse(txt_source)
    txt_functions = {
        node.name
        for node in txt_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    txt_imports_write_text_file = any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"helper_files", "w.helper_files"}
        and any(alias.name == "write_text_file" for alias in node.names)
        for node in txt_tree.body
    )
    txt_calls_write_text_file = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_text_file"
        for node in ast.walk(txt_tree)
    )
    p_txt_import_line = "from w.p_txt import process_text_pipeline"
    forbidden_p_imports = sorted(
        name for name in forbidden_p_import_names if name in p_source
    )
    forbidden_removed_imports = sorted(
        marker for marker in removed_import_markers if marker in p_source
    )
    forbidden_premium_txt_markers = sorted(
        marker for marker in forbidden_premium_markers if marker in txt_source
    )
    forbidden_premium_p_markers = sorted(
        marker for marker in forbidden_premium_markers if marker in p_source
    )
    text_process_classes = sorted(
        node.name for node in ast.walk(txt_tree) if isinstance(node, ast.ClassDef)
    )
    forbidden_text_wiring_markers = {
        "for enabled, name, target, args",
        "RouteSpec",
        "SimpleNamespace",
        "@dataclass",
        "from dataclasses import",
        "functools.partial",
        "partial(",
        "LLMClient",
        "LLMService",
        "TextModelRunner",
    }
    forbidden_text_wiring = sorted(
        marker for marker in forbidden_text_wiring_markers if marker in txt_source
    )
    hardcoded_desktop = "/desktop" in txt_source
    call_llm_nodes = [
        node
        for node in ast.walk(txt_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "call_llm"
    ]
    inline_llm_option_keywords = sorted(
        {
            keyword.arg
            for node in call_llm_nodes
            for keyword in node.keywords
            if keyword.arg in {"max_retries", "timeout", "retry_delay"}
        }
    )
    call_text_llm_nodes = [
        node
        for node in txt_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "call_text_llm"
    ]
    call_llm_in_call_text_llm = [
        node
        for function_node in call_text_llm_nodes
        for node in ast.walk(function_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "call_llm"
    ]
    call_text_llm_option_keywords = sorted(
        {
            keyword.arg
            for node in call_llm_in_call_text_llm
            for keyword in node.keywords
            if keyword.arg in {"max_retries", "timeout", "retry_delay"}
        }
    )
    passed = (
        text_process_path.exists()
        and not outdated_text_process_path.exists()
        and not exposed_removed
        and not missing_required
        and not text_process_classes
        and "write_text_file" not in txt_functions
        and txt_imports_write_text_file
        and txt_calls_write_text_file
        and not forbidden_text_wiring
        and not hardcoded_desktop
        and call_llm_nodes
        and call_llm_nodes == call_llm_in_call_text_llm
        and call_text_llm_option_keywords == ["max_retries", "retry_delay", "timeout"]
        and inline_llm_option_keywords == ["max_retries", "retry_delay", "timeout"]
        and not removed_pretext_path.exists()
        and not removed_extract_path.exists()
        and not removed_distill_path.exists()
        and p_txt_import_line in p_source
        and p_source.count("from w.p_txt import") == 1
        and "process_text_pipeline(config, shutdown_flag)" in p_source
        and outdated_entrypoint not in p_source
        and outdated_module_name not in p_source
        and not forbidden_removed_imports
        and not any(marker in txt_source for marker in removed_import_markers)
        and "def run_distillation(" in txt_source
        and not forbidden_p_imports
        and not forbidden_premium_txt_markers
        and not forbidden_premium_p_markers
    )

    print_result(
        "text process module function boundary",
        passed,
        {
            "exposed_removed": exposed_removed,
            "missing_required": missing_required,
            "text_process_classes": text_process_classes,
            "defines_write_text_file": "write_text_file" in txt_functions,
            "imports_write_text_file": txt_imports_write_text_file,
            "calls_write_text_file": txt_calls_write_text_file,
            "forbidden_text_wiring": forbidden_text_wiring,
            "hardcoded_desktop": hardcoded_desktop,
            "call_text_llm_exists": bool(call_text_llm_nodes),
            "call_llm_in_call_text_llm": len(call_llm_in_call_text_llm),
            "call_text_llm_option_keywords": call_text_llm_option_keywords,
            "inline_llm_option_keywords": inline_llm_option_keywords,
            "text_process_exists": text_process_path.exists(),
            "outdated_text_process_exists": outdated_text_process_path.exists(),
            "removed_pretext_exists": removed_pretext_path.exists(),
            "removed_extract_exists": removed_extract_path.exists(),
            "removed_distill_exists": removed_distill_path.exists(),
            "forbidden_p_imports": forbidden_p_imports,
            "forbidden_removed_imports": forbidden_removed_imports,
            "forbidden_premium_txt_markers": forbidden_premium_txt_markers,
            "forbidden_premium_p_markers": forbidden_premium_p_markers,
        },
    )

    return passed, []


def test_text_process_compression_line_count(test_id: str) -> tuple[bool, list[Path]]:
    text_process_path = ROOT_DIR / "w" / "p_txt.py"
    line_count = len(text_process_path.read_text(encoding="utf-8").splitlines())
    max_lines = 500
    passed = line_count <= max_lines

    print_result(
        "text process compression line count",
        passed,
        {"line_count": line_count, "max_lines": max_lines, "baseline": 636},
    )

    return passed, []


def test_text_write_helper_cleanup_static(test_id: str) -> tuple[bool, list[Path]]:
    text_process_path = ROOT_DIR / "w" / "p_txt.py"
    helper_files_path = ROOT_DIR / "w" / "helper_files.py"
    txt_source = text_process_path.read_text(encoding="utf-8")
    helper_source = helper_files_path.read_text(encoding="utf-8")
    txt_tree = ast.parse(txt_source)
    helper_tree = ast.parse(helper_source)
    txt_functions = {
        node.name: node
        for node in txt_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    helper_functions = {
        node.name: node
        for node in helper_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    helper = helper_functions.get("write_text_file")
    txt_imports_write_text_file = any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"helper_files", "w.helper_files"}
        and any(alias.name == "write_text_file" for alias in node.names)
        for node in txt_tree.body
    )

    direct_write_release_lines = []
    lines = txt_source.splitlines()
    for index, line in enumerate(lines, 1):
        if (
            "with open(" in line
            and "encoding=" in line
            and ('"w"' in line or "'w'" in line)
        ):
            window = "\n".join(lines[index - 1 : index + 5])
            if "release_text_file_permissions(" in window:
                direct_write_release_lines.append(index)
    error_helper_name = "_write_" + "error_file"
    save_error_helper = txt_functions.get("save_pipeline_error")
    save_extract_helper = txt_functions.get("save_extract_result")
    pretext_helper = txt_functions.get("process_pretext_file")
    save_error_start = save_error_helper.lineno if save_error_helper else 0
    save_error_end = save_error_helper.end_lineno if save_error_helper else 0

    def function_calls(function_node: ast.FunctionDef | None, name: str) -> bool:
        return bool(
            function_node
            and any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
                for node in ast.walk(function_node)
            )
        )

    save_extract_calls_write_text_file = function_calls(save_extract_helper, "write_text_file")
    pretext_calls_write_text_file = function_calls(pretext_helper, "write_text_file")
    release_call_functions = sorted(
        function_node.name
        for function_node in txt_functions.values()
        if function_calls(function_node, "release_text_file_permissions")
    )

    error_route_path_writes = []
    for index, line in enumerate(lines, 1):
        if (
            ".error" in line
            and "logging.error" not in line
            and not (save_error_start <= index <= save_error_end)
        ):
            error_route_path_writes.append(index)

    passed = (
        helper is not None
        and "write_text_file" not in txt_functions
        and txt_imports_write_text_file
        and save_error_helper is not None
        and save_extract_helper is not None
        and error_helper_name not in txt_source
        and not direct_write_release_lines
        and not save_extract_calls_write_text_file
        and pretext_calls_write_text_file
        and not error_route_path_writes
        and "_write_distill_error" not in txt_source
        and release_call_functions == [
            "process_extract_file",
            "process_pretext_file",
            "save_pipeline_error",
        ]
    )

    print_result(
        "text write helper cleanup static",
        passed,
        {
            "helper_exists": helper is not None,
            "txt_defines_write_text_file": "write_text_file" in txt_functions,
            "txt_imports_write_text_file": txt_imports_write_text_file,
            "direct_write_release_lines": direct_write_release_lines,
            "save_extract_helper_exists": save_extract_helper is not None,
            "save_extract_calls_write_text_file": save_extract_calls_write_text_file,
            "pretext_calls_write_text_file": pretext_calls_write_text_file,
            "error_route_path_writes": error_route_path_writes,
            "save_error_helper_exists": save_error_helper is not None,
            "old_error_helper_present": error_helper_name in txt_source,
            "distill_error_helper_exists": "_write_distill_error" in txt_source,
            "release_call_functions": release_call_functions,
        },
    )

    return passed, []


def test_call_text_llm_forwards_options(test_id: str) -> tuple[bool, list[Path]]:
    config = {
        **CONFIG,
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "LLM_MAX_RETRIES": 7,
            "LLM_TIMEOUT_SECONDS": 123,
            "LLM_RETRY_DELAY_SECONDS": 11,
        },
    }
    model = "evaluation-model"
    system_prompt = "evaluation system prompt"
    user_text = f"evaluation user text {test_id}"
    file_path = f"/tmp/{test_id}.txt"
    captured = {}

    original_call_llm = txt_process_module.call_llm

    try:
        def fake_call_llm(**kwargs) -> str:
            captured.update(kwargs)
            return f"mock llm result {test_id}"

        txt_process_module.call_llm = fake_call_llm

        result = txt_process_module.call_text_llm(
            config,
            model,
            system_prompt,
            user_text,
            file_path,
        )
    finally:
        txt_process_module.call_llm = original_call_llm

    passed = (
        result == f"mock llm result {test_id}"
        and captured.get("model") == model
        and captured.get("system_prompt") == system_prompt
        and captured.get("user_text") == user_text
        and captured.get("file_path") == file_path
        and captured.get("max_retries") == config["INTERVALS"]["LLM_MAX_RETRIES"]
        and captured.get("timeout") == config["INTERVALS"]["LLM_TIMEOUT_SECONDS"]
        and captured.get("retry_delay") == config["INTERVALS"]["LLM_RETRY_DELAY_SECONDS"]
    )

    print_result(
        "call text llm forwards options",
        passed,
        {
            "captured": captured,
            "result": result,
        },
    )

    return passed, []


def test_write_text_file_helper_contract(test_id: str) -> tuple[bool, list[Path]]:
    target = PATHS.download_target / f"{test_id}_write_text_helper.txt"
    newline_target = PATHS.download_target / f"{test_id}_write_text_helper_newline.txt"
    cleanup = [target, newline_target]
    content = f"exact helper content {test_id}\nlatin: café\ncjk: 中文\n"

    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    original_release = helper_files_module.release_text_file_permissions
    release_calls = []

    try:
        def fake_release(path) -> None:
            release_calls.append(path)

        helper_files_module.release_text_file_permissions = fake_release
        returned = helper_files_module.write_text_file(target, content)

        raw = target.read_bytes() if target.exists() else b""
        first_call_ok = (
            target.is_file()
            and raw == content.encode("utf-8")
            and raw.decode("utf-8") == content
            and release_calls == [target]
            and returned == target
        )

        release_calls.clear()
        newline_returned = helper_files_module.write_text_file(newline_target, "a\nb\n", newline="")
        newline_raw = newline_target.read_bytes() if newline_target.exists() else b""

        passed = (
            first_call_ok
            and newline_target.is_file()
            and newline_raw == b"a\nb\n"
            and newline_raw.decode("utf-8") == "a\nb\n"
            and release_calls == [newline_target]
            and newline_returned == newline_target
        )

        print_result(
            "write text file helper contract",
            passed,
            {
                "target": target,
                "newline_target": newline_target,
                "release_calls": release_calls,
                "returned": returned,
                "newline_returned": newline_returned,
            },
        )

        return passed, cleanup

    finally:
        helper_files_module.release_text_file_permissions = original_release


def test_save_pipeline_error_helper_contract(test_id: str) -> tuple[bool, list[Path]]:
    base_name = f"{test_id}_save_pipeline_error"
    model = "bad\tmodel\nname"
    model_marker = sanitize_filename(model)
    source = PATHS.watch / f"{base_name}_source.txt"
    error_file = PATHS.watch / f"{base_name}.error"
    model_error = PATHS.watch / f"{base_name}.{model_marker}.error"
    missing = PATHS.watch / f"{base_name}_missing.txt"
    cleanup = [source, error_file, model_error, missing]

    config = {**CONFIG, "WATCH_FOLDER": str(PATHS.watch)}
    PATHS.watch.mkdir(parents=True, exist_ok=True)
    source.write_text(f"source to rename {test_id}\n", encoding="utf-8")

    returned_error = txt_process_module.save_pipeline_error(
        config,
        "extract",
        base_name,
        f"model error {test_id}",
        filename=source.name,
        model=model,
        file_path=source,
    )
    returned_missing = txt_process_module.save_pipeline_error(
        config, "extract", base_name, f"missing error {test_id}", file_path=missing
    )
    returned_none = txt_process_module.save_pipeline_error(
        config, "extract", base_name, f"none error {test_id}", file_path=None
    )

    passed = (
        Path(returned_error or "") == error_file
        and error_file.is_file()
        and not source.exists()
        and not model_error.exists()
        and returned_missing is None
        and returned_none is None
    )

    print_result(
        "save pipeline error helper contract",
        passed,
        {
            "source": source,
            "error_file": error_file,
            "model_error": model_error,
            "returned_error": returned_error,
            "returned_missing": returned_missing,
            "returned_none": returned_none,
        },
    )

    return passed, cleanup


def test_extract_full_process_writes_extract_markdown_and_archives_input(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = extract_input_suffix()
    base_name = f"{test_id}_extract_full"
    model = "evaluation-model"

    source = PATHS.extract_watch / f"{base_name}{extract_suffix}"
    extract_output = PATHS.extract / f"{base_name}_{model}.txt"
    archived = PATHS.pretext_done / source.name
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [source, extract_output, archived]

    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.pretext_done.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy extract source {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "DISTILL_MODEL": {},
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "LLM_MAX_RETRIES": 5,
            "LLM_TIMEOUT_SECONDS": 55,
            "LLM_RETRY_DELAY_SECONDS": 7,
        },
        "EXTRACT_MODELS": {
            **CONFIG.get("EXTRACT_MODELS", {}),
            "CORE": [model],
        },
    }
    expected_llm_options = {
        "max_retries": config["INTERVALS"]["LLM_MAX_RETRIES"],
        "timeout": config["INTERVALS"]["LLM_TIMEOUT_SECONDS"],
        "retry_delay": config["INTERVALS"]["LLM_RETRY_DELAY_SECONDS"],
    }

    original_call_llm = txt_process_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    captured_llm_calls = []

    try:
        def fake_call_llm(**kwargs) -> str:
            captured_llm_calls.append(
                {
                    "model": kwargs.get("model"),
                    "system_prompt": kwargs.get("system_prompt"),
                    "max_retries": kwargs.get("max_retries"),
                    "timeout": kwargs.get("timeout"),
                    "retry_delay": kwargs.get("retry_delay"),
                }
            )
            if kwargs.get("system_prompt") == config["CLASSIFIER_PROMPT"]:
                return "CORE"
            return f"mock extract result {test_id}"

        txt_process_module.call_llm = fake_call_llm

        txt_process_module.process_extract_file(config, str(source))

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        cleanup.extend(notes)

        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        passed = (
            not source.exists()
            and archived.is_file()
            and not extract_output.exists()
            and note is not None
            and f"mock extract result {test_id}" in note_text
            and captured_llm_calls
            == [
                {
                    "model": config["PRETEXT_MODEL"],
                    "system_prompt": config["CLASSIFIER_PROMPT"],
                    **expected_llm_options,
                },
                {
                    "model": model,
                    "system_prompt": config["EXTRACT_PROMPT"],
                    **expected_llm_options,
                },
            ]
        )

        print_result(
            "extract full process writes extract markdown and archives input",
            passed,
            {
                "source": source,
                "extract_output": extract_output,
                "markdown": note,
                "archived": archived,
                "llm_calls": captured_llm_calls,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()

def test_extract_failure_renames_source_in_place(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = extract_input_suffix()
    base_name = f"{test_id}_extract_fail"
    model = "evaluation-failing-model"
    watch_dir = ROOT_DIR / f"{test_id}_extract_fail_watch"

    source = watch_dir / f"{base_name}{extract_suffix}"
    error_file = watch_dir / f"{base_name}.error"
    per_model_error = PATHS.watch / f"{base_name}.{sanitize_filename(model)}.error"
    top_level_error = PATHS.watch / f"{base_name}.error"

    cleanup = [source, error_file, per_model_error, top_level_error, watch_dir]

    watch_dir.mkdir(parents=True, exist_ok=True)
    PATHS.watch.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy extract failure source {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "EXTRACT_WATCH_FOLDER": str(watch_dir),
        "DISTILL_MODEL": {},
        "EXTRACT_MODELS": {
            **CONFIG.get("EXTRACT_MODELS", {}),
            "CORE": [model],
        },
    }

    original_call_llm = txt_process_module.call_llm
    call_sequence = []

    try:
        def fail_call_llm(**kwargs):
            call_sequence.append((kwargs.get("system_prompt"), kwargs.get("model")))
            if kwargs.get("system_prompt") == config["CLASSIFIER_PROMPT"]:
                return "CORE"
            raise RuntimeError(f"mock extract failure {test_id}")

        txt_process_module.call_llm = fail_call_llm

        raised = False
        try:
            txt_process_module.process_extract_file(config, str(source))
        except RuntimeError:
            raised = True

        passed = (
            raised
            and not source.exists()
            and error_file.is_file()
            and not per_model_error.exists()
            and not top_level_error.exists()
            and call_sequence
            == [
                (config["CLASSIFIER_PROMPT"], config["PRETEXT_MODEL"]),
                (config["EXTRACT_PROMPT"], model),
            ]
        )

        print_result(
            "extract failure renames source in place",
            passed,
            {
                "source": source,
                "error_file": error_file,
                "per_model_error": per_model_error,
                "top_level_error": top_level_error,
                "raised": raised,
                "call_sequence": call_sequence,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

def test_text_worker_scans_route_text_inputs(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = extract_input_suffix()

    pretext_dir = ROOT_DIR / f"{test_id}_route_pretext_watch"
    extract_dir = ROOT_DIR / f"{test_id}_route_extract_watch"
    long_base = f"{test_id}_" + "x" * 70
    raw = pretext_dir / f"{long_base}{pretext_suffix}"
    renamed = pretext_dir / f"{sanitize_and_trim_filename(long_base)}{pretext_suffix}"
    extract = extract_dir / f"{test_id}_scan_existing{extract_suffix}"
    pretext_error = pretext_dir / f"{test_id}_pretext_scan.error"
    extract_error = extract_dir / f"{test_id}_extract_scan.error"

    cleanup = [raw, renamed, extract, pretext_error, extract_error, pretext_dir, extract_dir]

    pretext_dir.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    raw.write_text(f"startup scan raw {test_id}\n", encoding="utf-8")
    extract.write_text(f"startup scan extract {test_id}\n", encoding="utf-8")
    pretext_error.write_text(f"startup scan pretext error {test_id}\n", encoding="utf-8")
    extract_error.write_text(f"startup scan extract error {test_id}\n", encoding="utf-8")

    pretext_queue = Queue()
    extract_queue = Queue()
    processed_files_global = set()
    processed_files_lock = threading.Lock()
    config = extract_text_config(PRETEXT_WATCH_FOLDER=str(pretext_dir), EXTRACT_WATCH_FOLDER=str(extract_dir))
    txt_process_module.scan_text_files(config["PRETEXT_WATCH_FOLDER"], pretext_queue, config["PRETEXT_SUFFIX"], (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"]), processed_files_global, processed_files_lock)
    txt_process_module.scan_text_files(config["EXTRACT_WATCH_FOLDER"], extract_queue, (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"]))

    pretext_paths = list(pretext_queue.queue)
    extract_paths = list(extract_queue.queue)

    passed = (
        not raw.exists()
        and renamed.is_file()
        and str(renamed.resolve()) in pretext_paths
        and str(extract.resolve()) in extract_paths
        and str(pretext_error.resolve()) not in pretext_paths
        and str(extract_error.resolve()) not in extract_paths
    )

    print_result(
        "text worker scans route text inputs",
        passed,
        {
            "renamed": renamed,
            "pretext_error": pretext_error,
            "extract_error": extract_error,
            "pretext_queue": pretext_queue.qsize(),
            "extract_queue": extract_queue.qsize(),
        },
    )

    return passed, cleanup


def test_torrent_scan_moves_torrent(test_id: str) -> tuple[bool, list[Path]]:
    watch_dir = ROOT_DIR / f"{test_id}_torrent_watch"
    whisper_dir = ROOT_DIR / f"{test_id}_torrent_whisper"
    pretext_dir = ROOT_DIR / f"{test_id}_pretext_watch"
    extract_dir = ROOT_DIR / f"{test_id}_extract_watch"

    filename = f"{test_id}_ownership.torrent"
    source = watch_dir / filename
    target = whisper_dir / filename

    cleanup = [watch_dir, whisper_dir, pretext_dir, extract_dir]

    for folder in (watch_dir, whisper_dir, pretext_dir, extract_dir):
        folder.mkdir(parents=True, exist_ok=True)

    source.write_text(f"torrent ownership source {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "WATCH_FOLDER": watch_dir,
        "WHISPER_FOLDER": whisper_dir,
        "PRETEXT_WATCH_FOLDER": pretext_dir,
        "EXTRACT_WATCH_FOLDER": extract_dir,
    }

    moved_count = scan_torrent_watch_folder(config)

    passed = (
        moved_count == 1
        and not source.exists()
        and target.is_file()
    )

    print_result(
        "torrent scan moves torrent",
        passed,
        {
            "moved_count": moved_count,
            "target": target,
        },
    )

    return passed, cleanup


def test_text_workers_own_scan_functions(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = extract_input_suffix()

    raw = PATHS.pretext_watch / f"{test_id}_periodic_raw{pretext_suffix}"
    extract = PATHS.extract_watch / f"{test_id}_periodic_extract{extract_suffix}"

    cleanup = [raw, extract]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)

    raw.write_text(f"periodic scan raw {test_id}\n", encoding="utf-8")
    extract.write_text(f"periodic scan extract {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "PIPELINES": {
            **CONFIG["PIPELINES"],
            "TORRENT": False,
            "TTML": False,
            "AUDIO": False,
            "PRETEXT": True,
            "EXTRACT": True,
            "WIKI": False,
            "YTD": False,
        },
    }

    captured: dict[str, object] = {}
    captured_scan_args: dict[str, tuple] = {}
    captured_queues = {}
    original_process_queue = txt_process_module.process_queue
    threads = {}
    shutdown_flag = threading.Event()

    try:
        def fake_process_queue(_config, queue, _process, method_name, scan_files=None, shutdown_flag=None, *scan_args):
            captured[method_name] = getattr(scan_files, "func", scan_files)
            captured_scan_args[method_name] = scan_args
            captured_queues[method_name] = queue

        txt_process_module.process_queue = fake_process_queue
        threads = txt_process_module.process_text_pipeline(config, shutdown_flag)
        for thread in threads.values():
            thread.join(timeout=1)
    finally:
        shutdown_flag.set()
        txt_process_module.process_queue = original_process_queue

    pretext_scan_args = captured_scan_args.get("process_pretext", ())
    extract_scan_args = captured_scan_args.get("process_extract", ())
    lock_type = type(threading.Lock())
    passed = (
        captured == {
            "process_pretext": txt_process_module.scan_text_files,
            "process_extract": txt_process_module.scan_text_files,
        }
        and len(pretext_scan_args) == 6
        and pretext_scan_args[0] == config["PRETEXT_WATCH_FOLDER"]
        and pretext_scan_args[1] is captured_queues["process_pretext"]
        and pretext_scan_args[2] == config["PRETEXT_SUFFIX"]
        and pretext_scan_args[3] == (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"])
        and isinstance(pretext_scan_args[4], set)
        and isinstance(pretext_scan_args[5], lock_type)
        and len(extract_scan_args) == 3
        and extract_scan_args[0] == config["EXTRACT_WATCH_FOLDER"]
        and extract_scan_args[1] is captured_queues["process_extract"]
        and extract_scan_args[2] == (config["EXTRACT_SUFFIX"], config["PREMIUM_SUFFIX"])
        and set(threads) == {
            "TextPipeline-Pretext",
            "TextPipeline-Extract",
        }
        and captured_queues["process_pretext"].empty()
        and captured_queues["process_extract"].empty()
        and raw.is_file()
        and extract.is_file()
    )

    print_result(
        "text workers own scan functions",
        passed,
        {
            "raw": raw,
            "extract": extract,
            "captured": sorted(captured),
            "pretext_scan_args": pretext_scan_args,
            "extract_scan_args": extract_scan_args,
        },
    )

    return passed, cleanup


def test_audio_scan_enqueues_audio_file(test_id: str) -> tuple[bool, list[Path]]:
    folder = PATHS.audio_watch_folders[0]
    source = folder / f"{test_id}_scan_audio.mp3"
    audio_queue = Queue()

    cleanup = [source]

    folder.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy audio scan source {test_id}\n", encoding="utf-8")
    scan_audio_files(CONFIG, audio_queue)

    queued_items = list(audio_queue.queue)
    queued_paths = [item[0] for item in queued_items]

    passed = str(source) in queued_paths

    print_result(
        "audio scan enqueues audio file",
        passed,
        {
            "source": source,
            "queued": passed,
            "queue_size": audio_queue.qsize(),
        },
    )

    while not audio_queue.empty():
        audio_queue.get_nowait()
        audio_queue.task_done()

    return passed, cleanup


def test_audio_pipeline_scans_audio(test_id: str) -> tuple[bool, list[Path]]:
    folder = PATHS.audio_watch_folders[0]
    source = folder / f"{test_id}_audio_pipeline_scan.mp3"

    cleanup = [source]

    folder.mkdir(parents=True, exist_ok=True)
    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)

    source.write_text(f"audio ownership scan source {test_id}\n", encoding="utf-8")

    original_process_audio_queue = audio_module.process_audio_queue
    queued_by_audio_pipeline: list[str] = []
    shutdown_flag = threading.Event()
    audio_queue = Queue()
    audio_processing_lock = threading.Lock()
    thread = None

    try:
        config = {
            **CONFIG,
            "INTERVALS": {
                **CONFIG["INTERVALS"],
                "WAIT_SECONDS": 0.01,
            },
        }
        def fake_process_audio_queue(
            _config,
            audio_queue,
            *,
            processing_lock,
            done_folder_path,
            shutdown_flag=None,
            once=False,
            wait_seconds=None,
        ) -> None:
            queued_by_audio_pipeline.extend(item[0] for item in list(audio_queue.queue))
            shutdown_flag.set()

        audio_module.process_audio_queue = fake_process_audio_queue

        thread = threading.Thread(
            target=orchestrator_module.process_audio_pipeline,
            args=(config, audio_queue, audio_processing_lock, shutdown_flag),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=1)

        passed = (
            str(source) in queued_by_audio_pipeline
            and orchestrator_module.process_audio_pipeline is audio_module.process_audio_pipeline
            and not thread.is_alive()
        )

        print_result(
            "audio pipeline scans audio",
            passed,
            {
                "source": source,
                "audio_pipeline_queued": str(source) in queued_by_audio_pipeline,
                "thread_alive": thread.is_alive(),
            },
        )

        return passed, cleanup

    finally:
        shutdown_flag.set()
        if thread is not None:
            thread.join(timeout=1)
        audio_module.process_audio_queue = original_process_audio_queue


def test_audio_process_file_mocked_full_path(test_id: str) -> tuple[bool, list[Path]]:
    folder = PATHS.audio_watch_folders[0]
    base_name = f"{test_id}_audio_full"
    source = folder / f"{base_name}.mp3"
    wav = PATHS.watch / f"{base_name}.wav"
    txt = PATHS.audio_transcribed / f"{base_name}{str(CONFIG['PRETEXT_SUFFIX']).lower()}"
    target = PATHS.audio_done / source.name

    cleanup = [source, wav, txt, target]

    folder.mkdir(parents=True, exist_ok=True)
    PATHS.watch.mkdir(parents=True, exist_ok=True)
    PATHS.audio_transcribed.mkdir(parents=True, exist_ok=True)
    PATHS.audio_done.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy audio full source {test_id}\n", encoding="utf-8")

    original_convert = audio_module.convert_audio_to_wav
    original_get_service = audio_module.get_service

    class MockService:
        def transcribe_file(self, wav_path: str) -> str:
            return f"mock transcription result {test_id} from {Path(wav_path).name}"

    try:
        def fake_convert(_folder_path: str, _audio_file: str) -> str:
            wav.write_text(f"dummy wav for {test_id}\n", encoding="utf-8")
            return str(wav)

        audio_module.convert_audio_to_wav = fake_convert
        audio_module.get_service = lambda: MockService()

        success = audio_module.process_audio_file(
            str(source),
            str(folder),
            CONFIG,
            str(PATHS.audio_done),
        )

        txt_text = txt.read_text(encoding="utf-8") if txt.exists() else ""

        passed = (
            success
            and not source.exists()
            and not wav.exists()
            and txt.is_file()
            and target.is_file()
            and f"mock transcription result {test_id}" in txt_text
        )

        print_result(
            "audio process file mocked full path",
            passed,
            {
                "source": source,
                "wav": wav,
                "txt": txt,
                "target": target,
                "success": success,
            },
        )

        return passed, cleanup

    finally:
        audio_module.convert_audio_to_wav = original_convert
        audio_module.get_service = original_get_service


def test_process_queue_handles_lock_miss_errors_and_permanent_failures(test_id: str) -> tuple[bool, list[Path]]:
    cleanup: list[Path] = []
    config = {
        **CONFIG,
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "WAIT_SECONDS": 0.01,
            "SCAN_SECONDS": 0,
        },
    }
    pretext_queue = Queue()
    shutdown_flag = threading.Event()

    lock_retry = f"{test_id}_queue_lock_retry"
    transient_error = f"{test_id}_queue_transient_error"
    permanent_error = f"{test_id}_queue_permanent_error"
    success = f"{test_id}_queue_success"
    for item in (lock_retry, transient_error, permanent_error, success):
        pretext_queue.put(item)

    processed: list[str] = []
    raised: list[str] = []
    primed_lock = threading.Lock()
    primed_lock.acquire()
    released_primed_lock = False

    class FakeHandler:
        def process_pretext(self, file_path: str) -> None:
            if file_path == transient_error:
                raised.append(file_path)
                raise RuntimeError(f"transient queue failure {test_id}")
            if file_path == permanent_error:
                raised.append(file_path)
                raise LLMPermanentFailure(
                    f"permanent queue failure {test_id}",
                    model="evaluation-model",
                    file_path=file_path,
                    reason="mock permanent failure",
                )
            processed.append(file_path)
            if success in processed and lock_retry in processed:
                shutdown_flag.set()

    try:
        def release_lock_scan() -> None:
            nonlocal released_primed_lock
            queued_items = list(pretext_queue.queue)
            if (
                not released_primed_lock
                and lock_retry in queued_items
                and queued_items[0] != lock_retry
            ):
                primed_lock.release()
                released_primed_lock = True

        with txt_process_module._file_locks_mutex:
            txt_process_module._file_locks[lock_retry] = primed_lock

        txt_process_module.process_queue(config, pretext_queue, FakeHandler().process_pretext, "process_pretext", release_lock_scan, shutdown_flag)
        stopped = shutdown_flag.is_set()

        passed = (
            stopped
            and processed == [success, lock_retry]
            and raised == [transient_error, permanent_error]
            and pretext_queue.empty()
        )

        print_result(
            "process queue handles lock miss errors and permanent failures",
            passed,
            {
                "lock_retry_delayed": released_primed_lock,
                "processed": processed,
                "raised": raised,
                "stopped": stopped,
            },
        )

        return passed, cleanup

    finally:
        with txt_process_module._file_locks_mutex:
            txt_process_module._file_locks.pop(lock_retry, None)
        if primed_lock.locked():
            primed_lock.release()


def test_distillation_success_skip_and_error_paths(test_id: str) -> tuple[bool, list[Path]]:
    model = "evaluation-distill-model"
    success_base = f"{test_id}_distill_success"
    skip_base = f"{test_id}_distill_skip"
    fail_base = f"{test_id}_distill_fail"

    success_extract = PATHS.extract / f"{success_base}_model-one.txt"
    expected_success_output = PATHS.extract / f"{success_base}_{model}.txt"
    fail_extract = PATHS.extract / f"{fail_base}_model-one.txt"
    md_path = PATHS.obsidian / f"{success_base}_260507.md"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [success_extract, expected_success_output, fail_extract, md_path]

    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    success_extract.write_text(f"extract content for {test_id}\n", encoding="utf-8")
    fail_extract.write_text(f"failing extract content for {test_id}\n", encoding="utf-8")
    md_path.write_text(f"# Existing distill note\n\nbody {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "DISTILL_MODEL": model,
        "DISTILL_PROMPT": "evaluation distill prompt",
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "LLM_MAX_RETRIES": 6,
            "LLM_TIMEOUT_SECONDS": 66,
            "LLM_RETRY_DELAY_SECONDS": 8,
        },
    }
    expected_llm_options = {
        "max_retries": config["INTERVALS"]["LLM_MAX_RETRIES"],
        "timeout": config["INTERVALS"]["LLM_TIMEOUT_SECONDS"],
        "retry_delay": config["INTERVALS"]["LLM_RETRY_DELAY_SECONDS"],
    }

    original_call_llm = txt_process_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    captured_llm_options = []
    captured_distill_prompts = []

    try:
        def fake_call_llm(**kwargs) -> str:
            captured_llm_options.append(
                {
                    "max_retries": kwargs.get("max_retries"),
                    "timeout": kwargs.get("timeout"),
                    "retry_delay": kwargs.get("retry_delay"),
                }
            )
            captured_distill_prompts.append(str(kwargs.get("user_text", "")))
            file_path = str(kwargs.get("file_path", ""))
            if fail_base in file_path:
                raise RuntimeError(f"mock distill failure {test_id}")
            return f"mock distilled result {test_id}"

        txt_process_module.call_llm = fake_call_llm

        success_path = txt_process_module.run_distillation(config, success_base, md_path=str(md_path))
        skip_path = txt_process_module.run_distillation(config, skip_base, md_path=None)

        failure_raised = False
        try:
            txt_process_module.run_distillation(config, fail_base, md_path=None)
        except RuntimeError:
            failure_raised = True

        error_file = PATHS.watch / f"{fail_base}.{sanitize_filename(model)}.error"
        cleanup.append(error_file)

        md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

        passed = (
            success_path is None
            and not expected_success_output.exists()
            and f"mock distilled result {test_id}" in md_text
            and skip_path is None
            and failure_raised
            and not error_file.exists()
            and len(captured_llm_options) == 2
            and len(captured_distill_prompts) == 2
            and all(options == expected_llm_options for options in captured_llm_options)
            and f"--- Extraction input 1: {success_extract.name} ---" in captured_distill_prompts[0]
            and f"--- Extraction input 1: {fail_extract.name} ---" in captured_distill_prompts[1]
        )

        print_result(
            "distillation success skip and error paths",
            passed,
            {
                "success_path": success_path,
                "expected_success_output_exists": expected_success_output.exists(),
                "skip_path": skip_path,
                "error_file": error_file,
                "failure_raised": failure_raised,
                "llm_options": captured_llm_options,
                "distill_headers": [
                    len(captured_distill_prompts) > 0 and f"--- Extraction input 1: {success_extract.name} ---" in captured_distill_prompts[0],
                    len(captured_distill_prompts) > 1 and f"--- Extraction input 1: {fail_extract.name} ---" in captured_distill_prompts[1],
                ],
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_extract_multi_model_failure_is_terminal(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = extract_input_suffix()
    base_name = f"{test_id}_extract_partial"
    bad_model = "evaluation-bad-model"
    later_model = "evaluation-later-model"
    watch_dir = ROOT_DIR / f"{test_id}_extract_partial_watch"

    source = watch_dir / f"{base_name}{extract_suffix}"
    error_file = watch_dir / f"{base_name}.error"
    bad_model_error = PATHS.watch / f"{base_name}.{sanitize_filename(bad_model)}.error"
    later_output = PATHS.extract / f"{base_name}_{later_model}.txt"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [source, error_file, bad_model_error, later_output, watch_dir]

    watch_dir.mkdir(parents=True, exist_ok=True)
    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.watch.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    source.write_text(f"partial extract source {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "EXTRACT_WATCH_FOLDER": str(watch_dir),
        "DISTILL_MODEL": {},
        "EXTRACT_MODELS": {
            **CONFIG.get("EXTRACT_MODELS", {}),
            "CORE": [bad_model, later_model],
        },
    }

    original_call_llm = txt_process_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    call_sequence = []

    try:
        def fake_call_llm(**kwargs) -> str:
            call_sequence.append((kwargs.get("system_prompt"), kwargs.get("model")))
            if kwargs.get("system_prompt") == config["CLASSIFIER_PROMPT"]:
                return "CORE"
            if kwargs.get("model") == bad_model:
                raise RuntimeError(f"mock partial failure {test_id}")
            return f"unexpected later success {test_id}"

        txt_process_module.call_llm = fake_call_llm

        raised = False
        try:
            txt_process_module.process_extract_file(config, str(source))
        except RuntimeError:
            raised = True

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        error_files = sorted(PATHS.watch.glob(f"{base_name}*.error"))
        cleanup.extend(notes)
        cleanup.extend(error_files)

        passed = (
            raised
            and not source.exists()
            and error_file.is_file()
            and not later_output.exists()
            and note is None
            and not bad_model_error.exists()
            and not error_files
            and call_sequence
            == [
                (config["CLASSIFIER_PROMPT"], config["PRETEXT_MODEL"]),
                (config["EXTRACT_PROMPT"], bad_model),
            ]
        )

        print_result(
            "extract multi model failure is terminal",
            passed,
            {
                "error_file": error_file,
                "later_output": later_output,
                "markdown": note,
                "error_files": error_files,
                "raised": raised,
                "call_sequence": call_sequence,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_extract_other_route_uses_other_models_and_distills(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = extract_input_suffix()
    base_name = f"{test_id}_extract_other"
    core_model = "evaluation-core-model"
    first_other_model = "evaluation-first-other-model"
    second_other_model = "evaluation-second-other-model"
    distill_model = "evaluation-distill-model"

    source = PATHS.extract_watch / f"{base_name}{extract_suffix}"
    archived = PATHS.pretext_done / source.name
    core_output = PATHS.extract / f"{base_name}_{core_model}.txt"
    first_output = PATHS.extract / f"{base_name}_{first_other_model}.txt"
    second_output = PATHS.extract / f"{base_name}_{second_other_model}.txt"
    distill_output = PATHS.extract / f"{base_name}_{distill_model}.txt"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [source, archived, core_output, first_output, second_output, distill_output]

    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.pretext_done.mkdir(parents=True, exist_ok=True)
    PATHS.watch.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    source.write_text(f"other route source {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "DISTILL_MODEL": {
            "OTHER": distill_model,
        },
        "EXTRACT_MODELS": {
            **CONFIG.get("EXTRACT_MODELS", {}),
            "CORE": [core_model],
            "OTHER": [first_other_model, second_other_model],
        },
    }

    original_call_llm = txt_process_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    call_sequence = []
    captured_distill_prompts = []

    try:
        def fake_call_llm(**kwargs) -> str:
            call_sequence.append((kwargs.get("system_prompt"), kwargs.get("model")))
            if kwargs.get("system_prompt") == config["CLASSIFIER_PROMPT"]:
                return "OTHER"
            if kwargs.get("model") in {first_other_model, second_other_model}:
                return f"mock other extract result {kwargs.get('model')} {test_id}"
            if kwargs.get("model") == distill_model:
                captured_distill_prompts.append(str(kwargs.get("user_text", "")))
                return f"mock distilled other result {test_id}"
            raise RuntimeError(f"unexpected LLM call: {kwargs.get('model')}")

        txt_process_module.call_llm = fake_call_llm

        txt_process_module.process_extract_file(config, str(source))

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        error_files = sorted(PATHS.watch.glob(f"{base_name}*.error"))
        cleanup.extend(notes)
        cleanup.extend(error_files)

        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        passed = (
            not source.exists()
            and archived.is_file()
            and not core_output.exists()
            and not first_output.exists()
            and not second_output.exists()
            and not distill_output.exists()
            and note is not None
            and f"mock other extract result {first_other_model} {test_id}" in note_text
            and f"mock other extract result {second_other_model} {test_id}" in note_text
            and f"mock distilled other result {test_id}" in note_text
            and not error_files
            and len(captured_distill_prompts) == 1
            and f"--- Extraction input 1: {base_name}_{sanitize_filename(first_other_model)}.txt ---" in captured_distill_prompts[0]
            and f"--- Extraction input 2: {base_name}_{sanitize_filename(second_other_model)}.txt ---" in captured_distill_prompts[0]
            and f"mock other extract result {first_other_model} {test_id}" in captured_distill_prompts[0]
            and f"mock other extract result {second_other_model} {test_id}" in captured_distill_prompts[0]
            and call_sequence
            == [
                (config["CLASSIFIER_PROMPT"], config["PRETEXT_MODEL"]),
                (config["EXTRACT_PROMPT"], first_other_model),
                (config["EXTRACT_PROMPT"], second_other_model),
                (config["DISTILL_PROMPT"], distill_model),
            ]
        )

        print_result(
            "extract OTHER route uses OTHER models and distills",
            passed,
            {
                "archived": archived,
                "core_output": core_output,
                "first_output": first_output,
                "second_output": second_output,
                "distill_output": distill_output,
                "markdown": note,
                "error_files": error_files,
                "distill_prompt_has_extracts": bool(captured_distill_prompts)
                and f"mock other extract result {first_other_model} {test_id}" in captured_distill_prompts[0]
                and f"mock other extract result {second_other_model} {test_id}" in captured_distill_prompts[0],
                "call_sequence": call_sequence,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_pretext_multichunk_and_failure_discards_processed_path(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = extract_input_suffix()
    success_base = f"{test_id}_pretext_multichunk"
    failure_base = f"{test_id}_pretext_failure"

    success_source = PATHS.pretext_watch / f"{success_base}{pretext_suffix}"
    success_output = PATHS.pretext_watch / f"{success_base}{extract_suffix}"
    success_archive = PATHS.original / f"{success_base}.txt"
    failure_source = PATHS.pretext_watch / f"{failure_base}{pretext_suffix}"
    failure_error = PATHS.pretext_watch / f"{failure_base}.error"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [success_source, success_output, success_archive, failure_source, failure_error]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    success_source.write_text(("success chunk text " + test_id + "\n") * 180, encoding="utf-8")
    failure_source.write_text(f"failure source {test_id}\n", encoding="utf-8")

    config = {
        **extract_text_config(),
        "PRETEXT_MODEL": "evaluation-pretext-model",
        "PRETEXT_PROMPT": "evaluation pretext prompt",
    }

    original_call_llm = txt_process_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    call_count = 0

    try:
        chunk_results = ["ALPHA_RESULT", "BRAVO_OUTPUT", "CHARLIE_TEXT"]

        def success_call_llm(**_kwargs) -> str:
            nonlocal call_count
            call_count += 1
            return chunk_results[call_count - 1]

        txt_process_module.call_llm = success_call_llm
        success_processed_files = {str(success_source.resolve())}
        success_processed_files_lock = threading.Lock()
        txt_process_module.process_pretext_file(
            config,
            str(success_source),
            success_processed_files,
            success_processed_files_lock,
        )

        notes = sorted(PATHS.obsidian.glob(f"{success_base}_*.md"))
        note = notes[-1] if notes else None
        cleanup.extend(notes)

        success_text = success_output.read_text(encoding="utf-8") if success_output.exists() else ""
        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        def empty_call_llm(**_kwargs) -> str:
            return ""

        txt_process_module.call_llm = empty_call_llm
        failure_processed_files = {str(failure_source.resolve())}
        failure_processed_files_lock = threading.Lock()

        failure_raised = False
        try:
            txt_process_module.process_pretext_file(
                config,
                str(failure_source),
                failure_processed_files,
                failure_processed_files_lock,
            )
        except ValueError:
            failure_raised = True

        passed = (
            call_count > 1
            and not success_source.exists()
            and success_archive.is_file()
            and success_output.is_file()
            and "ALPHA_RESULT" in success_text
            and "BRAVO_OUTPUT" in success_text
            and "CHARLIE_TEXT" in success_text
            and "ALPHA_RESULT" in note_text
            and str(success_source.resolve()) not in success_processed_files
            and failure_raised
            and not failure_source.exists()
            and failure_error.is_file()
            and str(failure_source.resolve()) not in failure_processed_files
        )

        print_result(
            "pretext multichunk and failure discards processed path",
            passed,
            {
                "call_count": call_count,
                "success_output": success_output,
                "failure_source": failure_source,
                "failure_error": failure_error,
                "failure_raised": failure_raised,
            },
        )

        return passed, cleanup

    finally:
        txt_process_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_audio_failure_paths_archive_or_cleanup(test_id: str) -> tuple[bool, list[Path]]:
    folder = PATHS.audio_watch_folders[0]
    convert_base = f"{test_id}_audio_convert_fail"
    transcribe_base = f"{test_id}_audio_transcribe_fail"

    convert_source = folder / f"{convert_base}.mp3"
    convert_done = PATHS.audio_done / convert_source.name
    transcribe_source = folder / f"{transcribe_base}.mp3"
    transcribe_wav = PATHS.watch / f"{transcribe_base}.wav"
    transcribe_txt = PATHS.audio_transcribed / f"{transcribe_base}{str(CONFIG['PRETEXT_SUFFIX']).lower()}"

    cleanup = [
        convert_source,
        convert_done,
        transcribe_source,
        transcribe_wav,
        transcribe_txt,
    ]

    folder.mkdir(parents=True, exist_ok=True)
    PATHS.watch.mkdir(parents=True, exist_ok=True)
    PATHS.audio_done.mkdir(parents=True, exist_ok=True)
    PATHS.audio_transcribed.mkdir(parents=True, exist_ok=True)

    convert_source.write_text(f"convert failure source {test_id}\n", encoding="utf-8")
    transcribe_source.write_text(f"transcribe failure source {test_id}\n", encoding="utf-8")

    original_convert = audio_module.convert_audio_to_wav
    original_get_service = audio_module.get_service

    class FailingService:
        def transcribe_file(self, _wav_path: str) -> str:
            raise RuntimeError(f"mock transcription failure {test_id}")

    try:
        audio_module.convert_audio_to_wav = lambda *_args: None
        convert_success = audio_module.process_audio_file(
            str(convert_source),
            str(folder),
            CONFIG,
            str(PATHS.audio_done),
        )

        def fake_convert(_folder_path: str, _audio_file: str) -> str:
            transcribe_wav.write_text(f"wav for failed transcription {test_id}\n", encoding="utf-8")
            return str(transcribe_wav)

        audio_module.convert_audio_to_wav = fake_convert
        audio_module.get_service = lambda: FailingService()
        transcribe_success = audio_module.process_audio_file(
            str(transcribe_source),
            str(folder),
            CONFIG,
            str(PATHS.audio_done),
        )

        passed = (
            not convert_success
            and convert_done.is_file()
            and not convert_source.exists()
            and not transcribe_success
            and transcribe_source.is_file()
            and not transcribe_wav.exists()
            and not transcribe_txt.exists()
        )

        print_result(
            "audio failure paths archive or cleanup",
            passed,
            {
                "convert_success": convert_success,
                "convert_done": convert_done,
                "transcribe_success": transcribe_success,
                "transcribe_source": transcribe_source,
            },
        )

        return passed, cleanup

    finally:
        audio_module.convert_audio_to_wav = original_convert
        audio_module.get_service = original_get_service


def test_ttml_invalid_xml_restores_source_and_chinese_normalizes(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    invalid_source = PATHS.ttml_watch / f"{test_id}_invalid.ttml"
    invalid_output = PATHS.ttml_watch / f"{test_id}_invalid{pretext_suffix}"
    invalid_archive = PATHS.original / f"{test_id}_invalid.ttml"
    chinese_source = PATHS.ttml_watch / f"{test_id}_chinese.ttml"
    chinese_output = PATHS.ttml_watch / f"{test_id}_chinese{pretext_suffix}"
    chinese_archive = PATHS.original / f"{test_id}_chinese.ttml"

    cleanup = [
        invalid_source,
        invalid_output,
        invalid_archive,
        invalid_source.with_suffix(".ttml.processing"),
        chinese_source,
        chinese_output,
        chinese_archive,
        chinese_source.with_suffix(".ttml.processing"),
    ]

    PATHS.ttml_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)

    invalid_source.write_text("<tt><body><p>broken", encoding="utf-8")
    chinese_source.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tt>
  <body>
    <p>你 好</p>
    <p>世 界</p>
  </body>
</tt>
""",
        encoding="utf-8",
    )

    handle_ttml(
        str(invalid_source),
        str(PATHS.ttml_watch),
        str(PATHS.original),
        sanitize_and_trim_filename,
        pretext_suffix,
    )
    handle_ttml(
        str(chinese_source),
        str(PATHS.ttml_watch),
        str(PATHS.original),
        sanitize_and_trim_filename,
        pretext_suffix,
    )

    chinese_text = chinese_output.read_text(encoding="utf-8") if chinese_output.exists() else ""

    passed = (
        invalid_source.is_file()
        and not invalid_output.exists()
        and not invalid_archive.exists()
        and not invalid_source.with_suffix(".ttml.processing").exists()
        and not chinese_source.exists()
        and chinese_output.is_file()
        and chinese_archive.is_file()
        and "你好" in chinese_text
        and "世界" in chinese_text
        and "你 好" not in chinese_text
        and "世 界" not in chinese_text
    )

    print_result(
        "ttml invalid xml restores source and chinese normalizes",
        passed,
        {
            "invalid_source": invalid_source,
            "chinese_output": chinese_output,
            "chinese_text": chinese_text,
        },
    )

    return passed, cleanup


def test_ytd_failure_fallback_and_remove_failure_paths(test_id: str) -> tuple[bool, list[Path]]:
    fallback_dir = PATHS.download_target / f"{test_id}_ytd_fallback"
    fallback_missing = fallback_dir / "x.txt"
    fallback_active = fallback_dir / "X.txt"
    failure_list = PATHS.download_target / f"{test_id}_ytd_download_fail.txt"
    remove_fail_list = PATHS.download_target / f"{test_id}_ytd_remove_fail.txt"
    output = PATHS.download_target / f"{test_id}_ytd_remove_fail.mp4"

    cleanup = [fallback_active, fallback_missing, fallback_dir, failure_list, remove_fail_list, output]

    fallback_dir.mkdir(parents=True, exist_ok=True)
    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    fallback_url = f"https://www.youtube.com/watch?v={test_id}fb"
    failure_url = f"https://www.youtube.com/watch?v={test_id}dl"
    remove_fail_url = f"https://www.youtube.com/watch?v={test_id}rm"

    fallback_active.write_text(f"{fallback_url}\n", encoding="utf-8")
    failure_list.write_text(f"{failure_url}\n", encoding="utf-8")
    remove_fail_list.write_text(f"{remove_fail_url}\n", encoding="utf-8")

    found_url, active_path = read_next_download_url(fallback_missing, set())

    def run_download_loop(list_file: Path, fake_download, remove_line=None) -> tuple[str, bool]:
        config = {
            **CONFIG,
            "YTD_LIST_FILE": list_file,
            "DOWNLOAD_TARGET_FOLDER": PATHS.download_target,
            "INTERVALS": {
                **CONFIG["INTERVALS"],
                "SCAN_SECONDS": 0.05,
                "YTD_RESOLVE_TIMEOUT_SECONDS": 0.05,
            },
        }
        shutdown_flag = threading.Event()
        original_download = ytd_module.download
        original_remove_line = ytd_module.remove_download_url_line
        try:
            ytd_module.download = fake_download
            if remove_line is not None:
                ytd_module.remove_download_url_line = remove_line

            thread = threading.Thread(
                target=process_ytd_pipeline,
                args=(config, shutdown_flag),
                daemon=True,
            )
            thread.start()
            time.sleep(0.2)
            shutdown_flag.set()
            thread.join(timeout=1)
            remaining = list_file.read_text(encoding="utf-8") if list_file.exists() else ""
            return remaining, thread.is_alive()
        finally:
            ytd_module.download = original_download
            ytd_module.remove_download_url_line = original_remove_line
            shutdown_flag.set()

    def fail_download(*_args, **_kwargs):
        raise RuntimeError(f"mock download failure {test_id}")

    failure_remaining, failure_alive = run_download_loop(failure_list, fail_download)

    def successful_download(*_args, **_kwargs):
        output.write_text(f"downloaded but remove failed {test_id}\n", encoding="utf-8")
        return str(output), None

    remove_remaining, remove_alive = run_download_loop(
        remove_fail_list,
        successful_download,
        remove_line=lambda *_args: False,
    )

    passed = (
        found_url == fallback_url
        and active_path == fallback_active
        and failure_url in failure_remaining
        and not failure_alive
        and output.is_file()
        and remove_fail_url in remove_remaining
        and not remove_alive
    )

    print_result(
        "ytd failure fallback and remove failure paths",
        passed,
        {
            "fallback_active": active_path,
            "failure_remaining": failure_url in failure_remaining,
            "remove_failure_remaining": remove_fail_url in remove_remaining,
            "threads_alive": (failure_alive, remove_alive),
        },
    )

    return passed, cleanup


def test_wikilink_cleaner_run_level_backup_dry_run_and_ontology(test_id: str) -> tuple[bool, list[Path]]:
    target_dir = PATHS.download_target / f"{test_id}_wikilink_target"
    backup_dir = PATHS.download_target / f"{test_id}_wikilink_backup"
    valid_note = target_dir / f"{test_id}_valid.md"
    source = target_dir / f"W {test_id} links.md"
    ontology = target_dir / f"{test_id}_ontology.md"
    moved_ontology = target_dir / "Ontology" / ontology.name
    dry_source = target_dir / f"W {test_id} dry.md"
    limit_one = target_dir / f"W {test_id} limit one.md"
    limit_two = target_dir / f"W {test_id} limit two.md"

    cleanup = [
        valid_note,
        source,
        ontology,
        moved_ontology,
        dry_source,
        limit_one,
        limit_two,
        target_dir / "Ontology",
        backup_dir,
        target_dir,
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    valid_note.write_text(f"valid note {test_id}\n", encoding="utf-8")
    source.write_text(
        f"Keep [[{test_id}_valid]]\n"
        f"Embedded ![[{test_id}_missing_image]]\n"
        "\n"
        f"[[{test_id}_missing_only]]\n"
        "\n"
        f"Mixed [[{test_id}_missing_mixed]] text\n",
        encoding="utf-8",
    )
    ontology.write_text(f"Class:: {test_id}\n", encoding="utf-8")

    run_stats = clean_dead_links(
        str(target_dir),
        backup_dir=str(backup_dir),
        create_backup=True,
        dry_run=False,
        max_files=50,
    )

    backups = sorted(backup_dir.glob(f"*{test_id}*.md"))
    cleanup.extend(backups)
    source_text = source.read_text(encoding="utf-8") if source.exists() else ""

    dry_source.write_text(f"Dry [[{test_id}_dry_missing]]\n", encoding="utf-8")
    dry_cleaner = WikilinkCleaner(str(target_dir), create_backup=False, dry_run=True)
    dry_cleaner.process_file(dry_source)
    dry_text = dry_source.read_text(encoding="utf-8")
    dry_stats = dry_cleaner.get_stats()

    limit_one.write_text(f"limit one {test_id}\n", encoding="utf-8")
    limit_two.write_text(f"limit two {test_id}\n", encoding="utf-8")
    limited_cleaner = WikilinkCleaner(str(target_dir), create_backup=False, max_files=1)
    limited_files = limited_cleaner.find_target_files()

    passed = (
        run_stats["files_processed"] >= 1
        and run_stats["broken_links_removed"] >= 2
        and moved_ontology.is_file()
        and len(backups) >= 2
        and f"[[{test_id}_valid]]" in source_text
        and f"![[{test_id}_missing_image]]" in source_text
        and f"[[{test_id}_missing_only]]" not in source_text
        and f"[[{test_id}_missing_mixed]]" not in source_text
        and f"![[{test_id}_missing_image]]\nMixed" in source_text
        and f"Dry [[{test_id}_dry_missing]]" in dry_text
        and dry_stats["broken_links_found"] == 1
        and dry_stats["broken_links_removed"] == 0
        and len(limited_files) == 1
    )

    print_result(
        "wikilink cleaner run level backup dry run and ontology",
        passed,
        {
            "run_stats": run_stats,
            "backups": len(backups),
            "dry_stats": dry_stats,
            "limited_files": len(limited_files),
        },
    )

    return passed, cleanup


def test_helper_files_and_text_boundaries(test_id: str) -> tuple[bool, list[Path]]:
    folder = PATHS.download_target / f"{test_id}_utils"
    gbk_file = folder / f"{test_id}_gbk.txt"
    rename_source = folder / f"{test_id}_rename_source.txt"
    rename_target = folder / f"{test_id}_rename_target.txt"
    numbered_initial = folder / f"{test_id}_numbered_e.txt"
    numbered_first = folder / f"{test_id}_numbered_e_1.txt"
    numbered_second = folder / f"{test_id}_numbered_e_2.txt"

    cleanup = [
        gbk_file,
        rename_source,
        rename_target,
        numbered_initial,
        numbered_first,
        numbered_second,
        folder,
    ]

    folder.mkdir(parents=True, exist_ok=True)

    gbk_file.write_bytes(f"中文编码 {test_id}".encode("gbk"))
    content, encoding_used = read_file_with_encodings(str(gbk_file))

    rename_source.write_text(f"source {test_id}\n", encoding="utf-8")
    rename_target.write_text(f"target {test_id}\n", encoding="utf-8")
    rename_result = safe_rename(str(rename_source), str(rename_target))

    numbered_initial.write_text("initial\n", encoding="utf-8")
    numbered_first.write_text("first\n", encoding="utf-8")
    next_path = Path(get_next_available_filename(str(folder), f"{test_id}_numbered", "_e"))

    reserved_name = sanitize_filename("CON")
    invalid_name = sanitize_filename(f"bad/name:{test_id}")
    empty_name = sanitize_filename("   ")
    trimmed_name = sanitize_and_trim_filename(f"{test_id}_" + "x" * 80, max_length=30)

    passed = (
        content == f"中文编码 {test_id}"
        and encoding_used.lower() in {"gbk", "gb18030"}
        and rename_result == str(rename_source)
        and rename_source.is_file()
        and rename_target.read_text(encoding="utf-8") == f"target {test_id}\n"
        and next_path == numbered_second
        and reserved_name == "CON_"
        and "/" not in invalid_name
        and ":" not in invalid_name
        and empty_name == "untitled"
        and len(trimmed_name) <= 30
    )

    print_result(
        "utils files and text boundaries",
        passed,
        {
            "encoding_used": encoding_used,
            "rename_result": rename_result,
            "next_path": next_path,
            "reserved_name": reserved_name,
        },
    )

    return passed, cleanup


def test_start_system_creates_expected_threads_and_stop(test_id: str) -> tuple[bool, list[Path]]:
    cleanup: list[Path] = []

    expected_threads = {
        "TorrentPipeline",
        "TTMLPipeline",
        "TextPipeline-Pretext",
        "TextPipeline-Extract",
        "AudioPipeline-GPU",
        "WikilinkCleaner",
    }
    if CONFIG["PIPELINES"]["YTD"]:
        expected_threads.add("YTDPipeline")

    started_workers: set[str] = set()

    def fake_worker(_config, *args) -> None:
        started_workers.add(threading.current_thread().name)
        for value in args:
            if hasattr(value, "wait") and hasattr(value, "is_set"):
                value.wait(5)
                return

    def fake_queue_worker(_config, _queue, _process, _method_name, scan_files=None, shutdown_flag=None, *scan_args) -> None:
        started_workers.add(threading.current_thread().name)
        shutdown_flag.wait(5)

    original_values = {
        "process_torrent_pipeline": orchestrator_module.process_torrent_pipeline,
        "process_ttml_pipeline": orchestrator_module.process_ttml_pipeline,
        "process_queue": txt_process_module.process_queue,
        "process_audio_pipeline": orchestrator_module.process_audio_pipeline,
        "process_wikilink_cleaning": orchestrator_module.process_wikilink_cleaning,
        "process_ytd_pipeline": orchestrator_module.process_ytd_pipeline,
    }

    shutdown_flag = None
    threads = {}

    try:
        orchestrator_module.process_torrent_pipeline = fake_worker
        orchestrator_module.process_ttml_pipeline = fake_worker
        txt_process_module.process_queue = fake_queue_worker
        orchestrator_module.process_audio_pipeline = fake_worker
        orchestrator_module.process_wikilink_cleaning = fake_worker
        orchestrator_module.process_ytd_pipeline = fake_worker

        threads, shutdown_flag = orchestrator_module.start_runtime(CONFIG)

        deadline = time.time() + 2
        while time.time() < deadline and started_workers != expected_threads:
            time.sleep(0.01)

        thread_names = set(threads)

        shutdown_flag.set()

        for thread in threads.values():
            thread.join(timeout=1)

        passed = (
            thread_names == expected_threads
            and started_workers == expected_threads
            and shutdown_flag.is_set()
            and all(not thread.is_alive() for thread in threads.values())
        )

        print_result(
            "start system creates expected threads and stop",
            passed,
            {
                "thread_names": sorted(thread_names),
                "started_workers": sorted(started_workers),
                "shutdown": shutdown_flag.is_set(),
                "threads_alive": [
                    name
                    for name, thread in threads.items()
                    if thread.is_alive()
                ],
            },
        )

        return passed, cleanup

    finally:
        if shutdown_flag is not None and not shutdown_flag.is_set():
            shutdown_flag.set()
        for thread in threads.values():
            thread.join(timeout=1)

        orchestrator_module.process_torrent_pipeline = original_values["process_torrent_pipeline"]
        orchestrator_module.process_ttml_pipeline = original_values["process_ttml_pipeline"]
        txt_process_module.process_queue = original_values["process_queue"]
        orchestrator_module.process_audio_pipeline = original_values["process_audio_pipeline"]
        orchestrator_module.process_wikilink_cleaning = original_values["process_wikilink_cleaning"]
        orchestrator_module.process_ytd_pipeline = original_values["process_ytd_pipeline"]

def test_start_system_pretext_extract_toggle_matrix(test_id: str) -> tuple[bool, list[Path]]:
    cleanup: list[Path] = []

    base_threads = {
        "TorrentPipeline",
        "TTMLPipeline",
        "AudioPipeline-GPU",
        "WikilinkCleaner",
    }
    if CONFIG["PIPELINES"]["YTD"]:
        base_threads.add("YTDPipeline")

    cases = [
        (False, False, base_threads),
        (
            True,
            False,
            base_threads | {"TextPipeline-Pretext"},
        ),
        (
            False,
            True,
            base_threads | {"TextPipeline-Extract"},
        ),
        (
            True,
            True,
            base_threads | {
                "TextPipeline-Pretext",
                "TextPipeline-Extract",
            },
        ),
    ]

    started_workers: set[str] = set()

    def fake_worker(_config, *args) -> None:
        started_workers.add(threading.current_thread().name)
        for value in args:
            if hasattr(value, "wait") and hasattr(value, "is_set"):
                value.wait(5)
                return

    def fake_queue_worker(_config, _queue, _process, _method_name, scan_files=None, shutdown_flag=None, *scan_args) -> None:
        started_workers.add(threading.current_thread().name)
        shutdown_flag.wait(5)

    original_values = {
        "process_torrent_pipeline": orchestrator_module.process_torrent_pipeline,
        "process_ttml_pipeline": orchestrator_module.process_ttml_pipeline,
        "process_queue": txt_process_module.process_queue,
        "process_audio_pipeline": orchestrator_module.process_audio_pipeline,
        "process_wikilink_cleaning": orchestrator_module.process_wikilink_cleaning,
        "process_ytd_pipeline": orchestrator_module.process_ytd_pipeline,
    }

    started_systems = []
    results = []

    try:
        orchestrator_module.process_torrent_pipeline = fake_worker
        orchestrator_module.process_ttml_pipeline = fake_worker
        txt_process_module.process_queue = fake_queue_worker
        orchestrator_module.process_audio_pipeline = fake_worker
        orchestrator_module.process_wikilink_cleaning = fake_worker
        orchestrator_module.process_ytd_pipeline = fake_worker

        for pretext_enabled, extract_enabled, expected_threads in cases:
            started_workers.clear()

            config = {
                **extract_text_config(),
                "PIPELINES": {
                    **CONFIG["PIPELINES"],
                    "PRETEXT": pretext_enabled,
                    "EXTRACT": extract_enabled,
                },
            }

            threads, shutdown_flag = orchestrator_module.start_runtime(config)
            started_systems.append((shutdown_flag, threads))

            deadline = time.time() + 2
            while time.time() < deadline and started_workers != expected_threads:
                time.sleep(0.01)

            thread_names = set(threads)

            shutdown_flag.set()

            for thread in threads.values():
                thread.join(timeout=1)

            case_passed = (
                thread_names == expected_threads
                and started_workers == expected_threads
                and config["PIPELINES"]["PRETEXT"] is pretext_enabled
                and config["PIPELINES"]["EXTRACT"] is extract_enabled
                and ("TextPipeline-Pretext" in thread_names) is pretext_enabled
                and ("TextPipeline-Extract" in thread_names) is extract_enabled
                and shutdown_flag.is_set()
                and all(not thread.is_alive() for thread in threads.values())
            )

            results.append(
                {
                    "pretext": pretext_enabled,
                    "extract": extract_enabled,
                    "passed": case_passed,
                    "threads": sorted(thread_names),
                }
            )

        passed = all(item["passed"] for item in results)

        print_result(
            "start system pretext extract toggle matrix",
            passed,
            {
                "cases": results,
            },
        )

        return passed, cleanup

    finally:
        for shutdown_flag, threads in started_systems:
            if not shutdown_flag.is_set():
                shutdown_flag.set()
            for thread in threads.values():
                thread.join(timeout=1)

        orchestrator_module.process_torrent_pipeline = original_values["process_torrent_pipeline"]
        orchestrator_module.process_ttml_pipeline = original_values["process_ttml_pipeline"]
        txt_process_module.process_queue = original_values["process_queue"]
        orchestrator_module.process_audio_pipeline = original_values["process_audio_pipeline"]
        orchestrator_module.process_wikilink_cleaning = original_values["process_wikilink_cleaning"]
        orchestrator_module.process_ytd_pipeline = original_values["process_ytd_pipeline"]

def main() -> int:
    test_id = f"EVAL_{uuid.uuid4().hex[:8]}"
    all_cleanup: list[Path] = []
    results: list[bool] = []

    try:
        for test in (
            test_torrent_move,
            test_torrent_move_avoids_overwrite,
            test_ttml_convert,
            test_ttml_plain_text_branch_converts_and_archives,
            test_ytd_list_remove_completed,
            test_ytd_pipeline_mocked_loop_removes_completed_url,
            test_ytd_uses_shared_write_text_file_static,
            test_ytd_remove_uses_shared_write_text_file_contract,
            test_wikilink_cleaner_removes_broken_link,
            test_markdown_merge_updates_index,
            test_helper_md_uses_shared_write_text_file_static,
            test_audio_move_to_done_removes_wav,
            test_pretext_scan_deduplicates_queue,
            test_pretext_full_process_writes_pretext_markdown_and_archive,
            test_pretext_partial_error_filename_and_content_unchanged,
            test_distill_collects_extract_outputs,
            test_distillation_read_error_raises_without_sidecar,
            test_extract_worker_scan_queues_candidate_once,
            test_text_process_module_function_boundary,
            test_text_process_compression_line_count,
            test_text_write_helper_cleanup_static,
            test_call_text_llm_forwards_options,
            test_write_text_file_helper_contract,
            test_save_pipeline_error_helper_contract,
            test_extract_full_process_writes_extract_markdown_and_archives_input,
            test_extract_failure_renames_source_in_place,
            test_text_worker_scans_route_text_inputs,
            test_torrent_scan_moves_torrent,
            test_text_workers_own_scan_functions,
            test_audio_scan_enqueues_audio_file,
            test_audio_pipeline_scans_audio,
            test_audio_process_file_mocked_full_path,
            test_process_queue_handles_lock_miss_errors_and_permanent_failures,
            test_distillation_success_skip_and_error_paths,
            test_extract_multi_model_failure_is_terminal,
            test_extract_other_route_uses_other_models_and_distills,
            test_pretext_multichunk_and_failure_discards_processed_path,
            test_audio_failure_paths_archive_or_cleanup,
            test_ttml_invalid_xml_restores_source_and_chinese_normalizes,
            test_ytd_failure_fallback_and_remove_failure_paths,
            test_wikilink_cleaner_run_level_backup_dry_run_and_ontology,
            test_helper_files_and_text_boundaries,
            test_start_system_creates_expected_threads_and_stop,
            test_start_system_pretext_extract_toggle_matrix,
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
