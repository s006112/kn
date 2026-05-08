from __future__ import annotations

import sys
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
W_DIR = Path(__file__).resolve().parent

for path in (ROOT_DIR, W_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import archive.p as orchestrator_module  # noqa: E402
from archive.p import CONFIG  # noqa: E402
import p_audio as audio_module  # noqa: E402
from p_audio import audio_queue, move_files_to_done, scan_audio_files  # noqa: E402
from p_context import create_pipeline_context  # noqa: E402
import p_distill as distill_module  # noqa: E402
from p_distill import _collect_extracts  # noqa: E402
from p_extract import ExtractHandler, PremiumExtractHandler  # noqa: E402
import p_pipelines as pipelines  # noqa: E402
from p_pipelines import (  # noqa: E402
    move_torrent_to_whisper,
    read_next_download_url,
    remove_download_url_line,
    scan_torrent_watch_folder,
)
import p_pretext as pretext_module  # noqa: E402
from p_pretext import release_pretext_request, request_pretext_processing  # noqa: E402
from p_ttml import handle_ttml  # noqa: E402
from utils_md import merge_to_markdown  # noqa: E402
from utils_text import sanitize_and_trim_filename, sanitize_filename  # noqa: E402
from utils_unlink import WikilinkCleaner, clean_dead_links  # noqa: E402
import p_extract as extract_module  # noqa: E402
from utils_files import (  # noqa: E402
    get_next_available_filename,
    read_file_with_encodings,
    safe_rename,
)
from helper.helper_llm import LLMPermanentFailure  # noqa: E402


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


def test_torrent_move_avoids_overwrite(test_id: str) -> tuple[bool, list[Path]]:
    filename = f"{test_id}_duplicate.torrent"
    source = PATHS.watch / filename
    existing_target = PATHS.whisper / filename
    collision_target = PATHS.whisper / f"{test_id}_duplicate_1.torrent"

    cleanup = [source, existing_target, collision_target]

    PATHS.watch.mkdir(parents=True, exist_ok=True)
    PATHS.whisper.mkdir(parents=True, exist_ok=True)

    source.write_text(f"new torrent source {test_id}\n", encoding="utf-8")
    existing_target.write_text(f"existing torrent target {test_id}\n", encoding="utf-8")

    moved = move_torrent_to_whisper(str(source), str(PATHS.whisper))

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

def test_x_url_download_pipeline_mocked_loop_removes_completed_url(test_id: str) -> tuple[bool, list[Path]]:
    url = f"https://www.youtube.com/watch?v={test_id}"
    list_file = PATHS.download_target / f"{test_id}_x_urls.txt"
    output = PATHS.download_target / f"{test_id}_downloaded.mp4"

    cleanup = [list_file, output]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    list_file.write_text(f"\nnot a url\n{url}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "X_URL_LIST_FILE": list_file,
        "DOWNLOAD_TARGET_FOLDER": PATHS.download_target,
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "SCAN_SECONDS": 0.05,
            "X_RESOLVE_TIMEOUT_SECONDS": 0.05,
        },
    }

    ctx = create_pipeline_context(config)
    original_download = pipelines.download

    try:
        def fake_download(
            _url: str,
            _quality: str,
            *,
            output_dir: Path,
            resolve_timeout: float,
        ) -> tuple[str, None]:
            output.write_text(f"fake download for {test_id}\n", encoding="utf-8")
            return str(output), None

        pipelines.download = fake_download

        thread = threading.Thread(
            target=pipelines.process_x_url_download_pipeline,
            args=(ctx,),
            daemon=True,
        )
        thread.start()

        deadline = time.time() + 2
        while time.time() < deadline:
            remaining = list_file.read_text(encoding="utf-8") if list_file.exists() else ""
            if output.exists() and url not in remaining:
                break
            time.sleep(0.05)

        ctx.shutdown_flag.set()
        thread.join(timeout=1)

        remaining = list_file.read_text(encoding="utf-8") if list_file.exists() else ""

        passed = (
            output.is_file()
            and url not in remaining
            and "not a url" in remaining
            and not thread.is_alive()
        )

        print_result(
            "x url download pipeline mocked loop removes completed url",
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
        pipelines.download = original_download
        ctx.shutdown_flag.set()

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


def test_markdown_merge_updates_index(test_id: str) -> tuple[bool, list[Path]]:
    note = PATHS.download_target / f"{test_id}_note.md"
    whisper_index = PATHS.download_target / f"{test_id}_Whisper_000000.md"

    cleanup = [note, whisper_index]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    note.write_text(f"# Existing\n\nbody for {test_id}\n", encoding="utf-8")
    whisper_index.write_text("# Whisper\n---\n", encoding="utf-8")

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


def test_audio_move_to_done_removes_wav(test_id: str) -> tuple[bool, list[Path]]:
    source = PATHS.download_target / f"{test_id}_audio.mp3"
    wav = PATHS.download_target / f"{test_id}_audio.wav"
    target = PATHS.audio_done / source.name

    cleanup = [source, wav, target]

    PATHS.download_target.mkdir(parents=True, exist_ok=True)
    PATHS.audio_done.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy audio source {test_id}\n", encoding="utf-8")
    wav.write_text(f"dummy wav temp {test_id}\n", encoding="utf-8")

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


def test_pretext_request_deduplicates_queue(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    source = PATHS.pretext_watch / f"{test_id}_pretext{pretext_suffix}"

    cleanup = [source]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy pretext queue source {test_id}\n", encoding="utf-8")

    ctx = create_pipeline_context(CONFIG)
    first = request_pretext_processing(ctx, str(source))
    second = request_pretext_processing(ctx, str(source))
    queued_path = ctx.pretext_queue.get_nowait()
    release_pretext_request(ctx, str(source))

    passed = (
        first
        and not second
        and ctx.pretext_queue.empty()
        and queued_path == str(source.resolve())
        and str(source.resolve()) not in ctx.processed_files_global
    )

    print_result(
        "pretext request deduplicates queue",
        passed,
        {
            "source": source,
            "first": first,
            "second": second,
            "queued_path": queued_path,
        },
    )

    return passed, cleanup

def test_pretext_full_process_writes_pretext_markdown_and_archive(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
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

    config = {
        **CONFIG,
        "MODEL_PRETEXT": "evaluation-model",
        "PRETEXT_PROMPT": "evaluation pretext prompt",
    }

    original_call_llm = pretext_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None

    try:
        pretext_module.call_llm = lambda **_kwargs: f"mock pretext result {test_id}"

        ctx = create_pipeline_context(config)
        pretext_module.process_pretext_file(ctx, str(source))

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
            and str(source.resolve()) not in ctx.processed_files_global
        )

        print_result(
            "pretext full process writes pretext markdown and archive",
            passed,
            {
                "source": source,
                "output": output,
                "markdown": note,
                "archived": archived,
            },
        )

        return passed, cleanup

    finally:
        pretext_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()

def test_distill_collects_extract_outputs(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    base_name = f"{test_id}_distill"
    first = PATHS.extract / f"{base_name}_model-alpha{pretext_suffix}"
    second = PATHS.extract / f"{base_name}_model-beta_1{pretext_suffix}"
    ignored = PATHS.extract / f"{test_id}_other_model{pretext_suffix}"

    cleanup = [first, second, ignored]

    PATHS.extract.mkdir(parents=True, exist_ok=True)

    first.write_text(f"alpha extract for {test_id}\n", encoding="utf-8")
    second.write_text(f"beta extract for {test_id}\n", encoding="utf-8")
    ignored.write_text(f"ignored extract for {test_id}\n", encoding="utf-8")

    extracts = _collect_extracts(str(PATHS.extract), base_name, pretext_suffix)
    labels = [label for label, _, _ in extracts]
    contents = [content for _, content, _ in extracts]
    paths = [Path(path) for _, _, path in extracts]

    passed = (
        len(extracts) == 2
        and labels == ["model-alpha", "model-beta"]
        and f"alpha extract for {test_id}" in contents[0]
        and f"beta extract for {test_id}" in contents[1]
        and paths == [first, second]
    )

    print_result(
        "distill collects extract outputs",
        passed,
        {
            "base_name": base_name,
            "labels": labels,
            "paths": paths,
        },
    )

    return passed, cleanup


def test_extract_handler_queues_candidate_once(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
    source = PATHS.extract_watch / f"{test_id}_extract{extract_suffix}"
    ignored = PATHS.download_target / f"{test_id}_ignored{extract_suffix}"

    cleanup = [source, ignored]

    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    source.write_text(f"extract queue candidate {test_id}\n", encoding="utf-8")
    ignored.write_text(f"wrong folder candidate {test_id}\n", encoding="utf-8")

    ctx = create_pipeline_context(CONFIG)
    handler = ExtractHandler(CONFIG, ctx.extract_queue)
    handler._queue_file(str(source))
    handler._queue_file(str(source))
    handler._queue_file(str(ignored))

    queued_paths = list(ctx.extract_queue.queue)

    passed = (
        queued_paths == [str(source)]
        and str(source) in handler.processed_files
        and str(ignored) not in handler.processed_files
    )

    print_result(
        "extract handler queues candidate once",
        passed,
        {
            "source": source,
            "ignored": ignored,
            "queued_paths": queued_paths,
        },
    )

    return passed, cleanup

def test_extract_full_process_writes_extract_markdown_and_archive(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
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
        **CONFIG,
        "MODEL_DISTILL": "",
        "MODEL_EXTRACT_MATRIX": {
            **CONFIG.get("MODEL_EXTRACT_MATRIX", {}),
            "EXTRACT_WATCH_FOLDER": [model],
        },
    }

    original_call_llm = extract_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None

    try:
        extract_module.call_llm = lambda **_kwargs: f"mock extract result {test_id}"

        ctx = create_pipeline_context(config)
        handler = ExtractHandler(config, ctx.extract_queue)
        handler.process_extract(str(source), get_next_available_filename)

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        cleanup.extend(notes)

        extract_text = extract_output.read_text(encoding="utf-8") if extract_output.exists() else ""
        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        passed = (
            not source.exists()
            and archived.is_file()
            and extract_output.is_file()
            and f"mock extract result {test_id}" in extract_text
            and note is not None
            and f"mock extract result {test_id}" in note_text
        )

        print_result(
            "extract full process writes extract markdown and archive",
            passed,
            {
                "source": source,
                "extract_output": extract_output,
                "markdown": note,
                "archived": archived,
            },
        )

        return passed, cleanup

    finally:
        extract_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()

def test_extract_failure_writes_error_and_moves_to_fail(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
    base_name = f"{test_id}_extract_fail"
    model = "evaluation-failing-model"

    source = PATHS.extract_watch / f"{base_name}{extract_suffix}"
    failed = PATHS.fail / source.name

    cleanup = [source, failed]

    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.fail.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy extract failure source {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "MODEL_DISTILL": "",
        "MODEL_EXTRACT_MATRIX": {
            **CONFIG.get("MODEL_EXTRACT_MATRIX", {}),
            "EXTRACT_WATCH_FOLDER": [model],
        },
    }

    original_call_llm = extract_module.call_llm

    try:
        def fail_call_llm(**_kwargs):
            raise RuntimeError(f"mock extract failure {test_id}")

        extract_module.call_llm = fail_call_llm

        ctx = create_pipeline_context(config)
        handler = ExtractHandler(config, ctx.extract_queue)

        raised = False
        try:
            handler.process_extract(str(source), get_next_available_filename)
        except RuntimeError:
            raised = True

        error_files = sorted(PATHS.pretext_watch.glob(f"{base_name}*.error"))
        cleanup.extend(error_files)

        error_text = "\n".join(
            path.read_text(encoding="utf-8") for path in error_files
        )

        passed = (
            raised
            and not source.exists()
            and failed.is_file()
            and len(error_files) >= 2
            and f"mock extract failure {test_id}" in error_text
        )

        print_result(
            "extract failure writes error and moves to fail",
            passed,
            {
                "source": source,
                "failed": failed,
                "error_files": error_files,
                "raised": raised,
            },
        )

        return passed, cleanup

    finally:
        extract_module.call_llm = original_call_llm

def test_premium_extract_full_process_archives_to_archive_folder(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
    base_name = f"{test_id}_premium_full"
    model = "evaluation-premium-model"

    source = PATHS.premium_watch / f"{base_name}{extract_suffix}"
    extract_output = PATHS.extract / f"{base_name}_{model}.txt"
    archived = PATHS.archive / source.name
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [source, extract_output, archived]

    PATHS.premium_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.archive.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    source.write_text(f"dummy premium extract source {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "MODEL_EXTRACT_MATRIX": {
            **CONFIG.get("MODEL_EXTRACT_MATRIX", {}),
            "PREMIUM_WATCH_FOLDER": [model],
        },
    }

    original_call_llm = extract_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None

    try:
        extract_module.call_llm = lambda **_kwargs: f"mock premium extract result {test_id}"

        ctx = create_pipeline_context(config)
        handler = PremiumExtractHandler(config, ctx.premium_extract_queue)
        handler.process_premium_extract(str(source), get_next_available_filename)

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        cleanup.extend(notes)

        extract_text = extract_output.read_text(encoding="utf-8") if extract_output.exists() else ""
        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        passed = (
            not source.exists()
            and archived.is_file()
            and extract_output.is_file()
            and f"mock premium extract result {test_id}" in extract_text
            and note is not None
            and f"mock premium extract result {test_id}" in note_text
        )

        print_result(
            "premium extract full process archives to archive folder",
            passed,
            {
                "source": source,
                "extract_output": extract_output,
                "markdown": note,
                "archived": archived,
                "archive_folder": PATHS.archive,
            },
        )

        return passed, cleanup

    finally:
        extract_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()

def test_file_scanner_routes_text_inputs(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])

    long_base = f"{test_id}_" + "x" * 70
    raw = PATHS.pretext_watch / f"{long_base}{pretext_suffix}"
    renamed = PATHS.pretext_watch / f"{sanitize_and_trim_filename(long_base)}{pretext_suffix}"
    extract = PATHS.extract_watch / f"{test_id}_scan_existing{extract_suffix}"
    premium = PATHS.premium_watch / f"{test_id}_scan_existing{extract_suffix}"

    cleanup = [raw, renamed, extract, premium]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.premium_watch.mkdir(parents=True, exist_ok=True)

    raw.write_text(f"startup scan raw {test_id}\n", encoding="utf-8")
    extract.write_text(f"startup scan extract {test_id}\n", encoding="utf-8")
    premium.write_text(f"startup scan premium {test_id}\n", encoding="utf-8")

    ctx = create_pipeline_context(CONFIG)
    original_scan_torrent = pipelines.scan_torrent_watch_folder

    try:
        pipelines.scan_torrent_watch_folder = lambda _config: 0
        pipelines.file_scanner(ctx)
    finally:
        pipelines.scan_torrent_watch_folder = original_scan_torrent

    pretext_paths = list(ctx.pretext_queue.queue)
    extract_paths = list(ctx.extract_queue.queue)
    premium_paths = list(ctx.premium_extract_queue.queue)

    passed = (
        not raw.exists()
        and renamed.is_file()
        and str(renamed.resolve()) in pretext_paths
        and str(extract) in extract_paths
        and str(premium) in premium_paths
    )

    print_result(
        "file scanner routes text inputs",
        passed,
        {
            "renamed": renamed,
            "pretext_queue": ctx.pretext_queue.qsize(),
            "extract_queue": ctx.extract_queue.qsize(),
            "premium_queue": ctx.premium_extract_queue.qsize(),
        },
    )

    return passed, cleanup


def test_periodic_file_scanner_routes_text_inputs(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])

    raw = PATHS.pretext_watch / f"{test_id}_periodic_raw{pretext_suffix}"
    extract = PATHS.extract_watch / f"{test_id}_periodic_extract{extract_suffix}"
    premium = PATHS.premium_watch / f"{test_id}_periodic_premium{extract_suffix}"

    cleanup = [raw, extract, premium]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.premium_watch.mkdir(parents=True, exist_ok=True)

    raw.write_text(f"periodic scan raw {test_id}\n", encoding="utf-8")
    extract.write_text(f"periodic scan extract {test_id}\n", encoding="utf-8")
    premium.write_text(f"periodic scan premium {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "INTERVALS": {
            **CONFIG["INTERVALS"],
            "SCAN_SECONDS": 0.05,
        },
    }

    ctx = create_pipeline_context(config)
    original_scan_torrent = pipelines.scan_torrent_watch_folder

    try:
        pipelines.scan_torrent_watch_folder = lambda _config: 0
        thread = threading.Thread(
            target=orchestrator_module.run_periodic_file_scanner,
            args=(ctx,),
            daemon=True,
        )
        thread.start()

        deadline = time.time() + 2
        while time.time() < deadline:
            pretext_paths = list(ctx.pretext_queue.queue)
            extract_paths = list(ctx.extract_queue.queue)
            premium_paths = list(ctx.premium_extract_queue.queue)

            if (
                str(raw.resolve()) in pretext_paths
                and str(extract) in extract_paths
                and str(premium) in premium_paths
            ):
                break

            time.sleep(0.05)

        pretext_paths = list(ctx.pretext_queue.queue)
        extract_paths = list(ctx.extract_queue.queue)
        premium_paths = list(ctx.premium_extract_queue.queue)

        ctx.shutdown_flag.set()
        thread.join(timeout=1)

    finally:
        pipelines.scan_torrent_watch_folder = original_scan_torrent
        ctx.shutdown_flag.set()

    passed = (
        str(raw.resolve()) in pretext_paths
        and str(extract) in extract_paths
        and str(premium) in premium_paths
        and not thread.is_alive()
    )

    print_result(
        "periodic file scanner routes text inputs",
        passed,
        {
            "raw": raw,
            "extract": extract,
            "premium": premium,
            "thread_alive": thread.is_alive(),
        },
    )

    return passed, cleanup


def test_audio_scan_enqueues_audio_file(test_id: str) -> tuple[bool, list[Path]]:
    folder = PATHS.audio_watch_folders[0]
    source = folder / f"{test_id}_scan_audio.mp3"

    cleanup = [source]

    folder.mkdir(parents=True, exist_ok=True)

    while not audio_queue.empty():
        audio_queue.get_nowait()
        audio_queue.task_done()

    source.write_text(f"dummy audio scan source {test_id}\n", encoding="utf-8")
    scan_audio_files(CONFIG)

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
            "WAIT_SECONDS": 0,
        },
    }
    ctx = create_pipeline_context(config)

    lock_retry = f"{test_id}_queue_lock_retry"
    transient_error = f"{test_id}_queue_transient_error"
    permanent_error = f"{test_id}_queue_permanent_error"
    success = f"{test_id}_queue_success"
    for item in (lock_retry, transient_error, permanent_error, success):
        ctx.pretext_queue.put(item)

    lock_attempts: list[str] = []
    processed: list[str] = []
    raised: list[str] = []

    class StopLoop(Exception):
        pass

    class FakeFileLock:
        def __init__(self, file_path: str):
            self.file_path = file_path

        def __enter__(self) -> bool:
            lock_attempts.append(self.file_path)
            return not (
                self.file_path == lock_retry
                and lock_attempts.count(lock_retry) == 1
            )

        def __exit__(self, *_args) -> bool:
            return False

    class FakeHandler:
        def process_pretext(self, file_path: str, _get_next_available_filename) -> None:
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

    original_file_lock = pipelines.file_lock
    original_sleep = pipelines.time.sleep

    try:
        def fake_sleep(_seconds: float) -> None:
            if ctx.pretext_queue.empty() and success in processed:
                raise StopLoop()
            original_sleep(0)

        pipelines.file_lock = lambda path: FakeFileLock(path)
        pipelines.time.sleep = fake_sleep

        stopped = False
        try:
            pipelines.process_queue(ctx, ctx.pretext_queue, FakeHandler().process_pretext, "process_pretext")
        except StopLoop:
            stopped = True

        passed = (
            stopped
            and lock_attempts.count(lock_retry) == 2
            and processed == [success, lock_retry]
            and raised == [transient_error, permanent_error]
            and ctx.pretext_queue.empty()
        )

        print_result(
            "process queue handles lock miss errors and permanent failures",
            passed,
            {
                "lock_retry_attempts": lock_attempts.count(lock_retry),
                "processed": processed,
                "raised": raised,
                "stopped": stopped,
            },
        )

        return passed, cleanup

    finally:
        pipelines.file_lock = original_file_lock
        pipelines.time.sleep = original_sleep


def test_distillation_success_skip_and_error_paths(test_id: str) -> tuple[bool, list[Path]]:
    model = "evaluation-distill-model"
    success_base = f"{test_id}_distill_success"
    skip_base = f"{test_id}_distill_skip"
    fail_base = f"{test_id}_distill_fail"

    success_extract = PATHS.extract / f"{success_base}_model-one.txt"
    fail_extract = PATHS.extract / f"{fail_base}_model-one.txt"
    md_path = PATHS.obsidian / f"{success_base}_260507.md"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [success_extract, fail_extract, md_path]

    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    success_extract.write_text(f"extract content for {test_id}\n", encoding="utf-8")
    fail_extract.write_text(f"failing extract content for {test_id}\n", encoding="utf-8")
    md_path.write_text(f"# Existing distill note\n\nbody {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "MODEL_DISTILL": model,
        "DISTILL_PROMPT": "evaluation distill prompt",
    }

    original_call_llm = distill_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None

    try:
        def fake_call_llm(**kwargs) -> str:
            file_path = str(kwargs.get("file_path", ""))
            if fail_base in file_path:
                raise RuntimeError(f"mock distill failure {test_id}")
            return f"mock distilled result {test_id}"

        distill_module.call_llm = fake_call_llm

        success_path = Path(
            distill_module.run_distillation(config, success_base, md_path=str(md_path))
            or ""
        )
        skip_path = distill_module.run_distillation(config, skip_base, md_path=None)

        failure_raised = False
        try:
            distill_module.run_distillation(config, fail_base, md_path=None)
        except RuntimeError:
            failure_raised = True

        error_file = PATHS.extract / f"{fail_base}_e.distill.error"
        cleanup.extend([success_path, error_file])

        success_text = success_path.read_text(encoding="utf-8") if success_path.exists() else ""
        md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        error_text = error_file.read_text(encoding="utf-8") if error_file.exists() else ""

        passed = (
            success_path.is_file()
            and f"mock distilled result {test_id}" in success_text
            and f"mock distilled result {test_id}" in md_text
            and skip_path is None
            and failure_raised
            and error_file.is_file()
            and f"mock distill failure {test_id}" in error_text
        )

        print_result(
            "distillation success skip and error paths",
            passed,
            {
                "success_path": success_path,
                "skip_path": skip_path,
                "error_file": error_file,
                "failure_raised": failure_raised,
            },
        )

        return passed, cleanup

    finally:
        distill_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_extract_multi_model_partial_failure_preserves_success_outputs(test_id: str) -> tuple[bool, list[Path]]:
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
    base_name = f"{test_id}_extract_partial"
    good_model = "evaluation-good-model"
    bad_model = "evaluation-bad-model"

    source = PATHS.extract_watch / f"{base_name}{extract_suffix}"
    good_output = PATHS.extract / f"{base_name}_{good_model}.txt"
    failed = PATHS.fail / source.name
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [source, good_output, failed]

    PATHS.extract_watch.mkdir(parents=True, exist_ok=True)
    PATHS.extract.mkdir(parents=True, exist_ok=True)
    PATHS.fail.mkdir(parents=True, exist_ok=True)
    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    source.write_text(f"partial extract source {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "MODEL_DISTILL": "",
        "MODEL_EXTRACT_MATRIX": {
            **CONFIG.get("MODEL_EXTRACT_MATRIX", {}),
            "EXTRACT_WATCH_FOLDER": [good_model, bad_model],
        },
    }

    original_call_llm = extract_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None

    try:
        def fake_call_llm(**kwargs) -> str:
            if kwargs.get("model") == bad_model:
                raise RuntimeError(f"mock partial failure {test_id}")
            return f"mock partial success {test_id}"

        extract_module.call_llm = fake_call_llm

        ctx = create_pipeline_context(config)
        handler = ExtractHandler(config, ctx.extract_queue)

        raised = False
        try:
            handler.process_extract(str(source), get_next_available_filename)
        except RuntimeError:
            raised = True

        notes = sorted(PATHS.obsidian.glob(f"{base_name}_*.md"))
        note = notes[-1] if notes else None
        error_files = sorted(PATHS.pretext_watch.glob(f"{base_name}*.error"))
        cleanup.extend(notes)
        cleanup.extend(error_files)

        output_text = good_output.read_text(encoding="utf-8") if good_output.exists() else ""
        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""
        error_text = "\n".join(
            path.read_text(encoding="utf-8") for path in error_files
        )

        passed = (
            raised
            and not source.exists()
            and failed.is_file()
            and good_output.is_file()
            and f"mock partial success {test_id}" in output_text
            and f"mock partial success {test_id}" in note_text
            and len(error_files) >= 2
            and f"mock partial failure {test_id}" in error_text
        )

        print_result(
            "extract multi model partial failure preserves success outputs",
            passed,
            {
                "good_output": good_output,
                "failed": failed,
                "markdown": note,
                "error_files": error_files,
                "raised": raised,
            },
        )

        return passed, cleanup

    finally:
        extract_module.call_llm = original_call_llm

        if index_before is not None:
            whisper_index.write_text(index_before, encoding="utf-8")
        elif not index_existed and whisper_index.exists():
            whisper_index.unlink()


def test_pretext_multichunk_and_failure_release_request(test_id: str) -> tuple[bool, list[Path]]:
    pretext_suffix = str(CONFIG["PRETEXT_SUFFIX"])
    extract_suffix = str(CONFIG["EXTRACT_SUFFIX"][0])
    success_base = f"{test_id}_pretext_multichunk"
    failure_base = f"{test_id}_pretext_failure"

    success_source = PATHS.pretext_watch / f"{success_base}{pretext_suffix}"
    success_output = PATHS.pretext_watch / f"{success_base}{extract_suffix}"
    success_archive = PATHS.original / f"{success_base}.txt"
    failure_source = PATHS.pretext_watch / f"{failure_base}{pretext_suffix}"
    whisper_index = PATHS.obsidian / "Whisper 000000.md"

    cleanup = [success_source, success_output, success_archive, failure_source]

    PATHS.pretext_watch.mkdir(parents=True, exist_ok=True)
    PATHS.original.mkdir(parents=True, exist_ok=True)
    PATHS.obsidian.mkdir(parents=True, exist_ok=True)

    success_source.write_text(("success chunk text " + test_id + "\n") * 180, encoding="utf-8")
    failure_source.write_text(f"failure source {test_id}\n", encoding="utf-8")

    config = {
        **CONFIG,
        "MODEL_PRETEXT": "evaluation-pretext-model",
        "PRETEXT_PROMPT": "evaluation pretext prompt",
    }

    original_call_llm = pretext_module.call_llm
    index_existed = whisper_index.exists()
    index_before = whisper_index.read_text(encoding="utf-8") if index_existed else None
    call_count = 0

    try:
        chunk_results = ["ALPHA_RESULT", "BRAVO_OUTPUT", "CHARLIE_TEXT"]

        def success_call_llm(**_kwargs) -> str:
            nonlocal call_count
            call_count += 1
            return chunk_results[call_count - 1]

        pretext_module.call_llm = success_call_llm
        success_ctx = create_pipeline_context(config)
        success_ctx.processed_files_global.add(str(success_source.resolve()))
        pretext_module.process_pretext_file(success_ctx, str(success_source))

        notes = sorted(PATHS.obsidian.glob(f"{success_base}_*.md"))
        note = notes[-1] if notes else None
        cleanup.extend(notes)

        success_text = success_output.read_text(encoding="utf-8") if success_output.exists() else ""
        note_text = note.read_text(encoding="utf-8") if note and note.exists() else ""

        def empty_call_llm(**_kwargs) -> str:
            return ""

        pretext_module.call_llm = empty_call_llm
        failure_ctx = create_pipeline_context(config)
        failure_ctx.processed_files_global.add(str(failure_source.resolve()))

        failure_raised = False
        try:
            pretext_module.process_pretext_file(failure_ctx, str(failure_source))
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
            and str(success_source.resolve()) not in success_ctx.processed_files_global
            and failure_raised
            and failure_source.is_file()
            and str(failure_source.resolve()) not in failure_ctx.processed_files_global
        )

        print_result(
            "pretext multichunk and failure release request",
            passed,
            {
                "call_count": call_count,
                "success_output": success_output,
                "failure_source": failure_source,
                "failure_raised": failure_raised,
            },
        )

        return passed, cleanup

    finally:
        pretext_module.call_llm = original_call_llm

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


def test_x_url_failure_fallback_and_remove_failure_paths(test_id: str) -> tuple[bool, list[Path]]:
    fallback_dir = PATHS.download_target / f"{test_id}_x_fallback"
    fallback_missing = fallback_dir / "x.txt"
    fallback_active = fallback_dir / "X.txt"
    failure_list = PATHS.download_target / f"{test_id}_x_download_fail.txt"
    remove_fail_list = PATHS.download_target / f"{test_id}_x_remove_fail.txt"
    output = PATHS.download_target / f"{test_id}_x_remove_fail.mp4"

    cleanup = [fallback_active, fallback_missing, fallback_dir, failure_list, remove_fail_list, output]

    fallback_dir.mkdir(parents=True, exist_ok=True)
    PATHS.download_target.mkdir(parents=True, exist_ok=True)

    fallback_url = f"https://www.youtube.com/watch?v={test_id}fb"
    failure_url = f"https://www.youtube.com/watch?v={test_id}dl"
    remove_fail_url = f"https://www.youtube.com/watch?v={test_id}rm"

    fallback_active.write_text(f"{fallback_url}\n", encoding="utf-8")
    failure_list.write_text(f"{failure_url}\n", encoding="utf-8")
    remove_fail_list.write_text(f"{remove_fail_url}\n", encoding="utf-8")

    found_url, active_path = pipelines.read_next_download_url(fallback_missing, set())

    def run_download_loop(list_file: Path, fake_download, remove_line=None) -> tuple[str, bool]:
        config = {
            **CONFIG,
            "X_URL_LIST_FILE": list_file,
            "DOWNLOAD_TARGET_FOLDER": PATHS.download_target,
            "INTERVALS": {
                **CONFIG["INTERVALS"],
                "SCAN_SECONDS": 0.05,
                "X_RESOLVE_TIMEOUT_SECONDS": 0.05,
            },
        }
        ctx = create_pipeline_context(config)
        original_download = pipelines.download
        original_remove_line = pipelines.remove_download_url_line
        try:
            pipelines.download = fake_download
            if remove_line is not None:
                pipelines.remove_download_url_line = remove_line

            thread = threading.Thread(
                target=pipelines.process_x_url_download_pipeline,
                args=(ctx,),
                daemon=True,
            )
            thread.start()
            time.sleep(0.2)
            ctx.shutdown_flag.set()
            thread.join(timeout=1)
            remaining = list_file.read_text(encoding="utf-8") if list_file.exists() else ""
            return remaining, thread.is_alive()
        finally:
            pipelines.download = original_download
            pipelines.remove_download_url_line = original_remove_line
            ctx.shutdown_flag.set()

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
        "x url failure fallback and remove failure paths",
        passed,
        {
            "fallback_active": active_path,
            "failure_remaining": failure_url in failure_remaining,
            "remove_failure_remaining": remove_fail_url in remove_remaining,
            "threads_alive": (failure_alive, remove_alive),
        },
    )

    return passed, cleanup


def test_wikilink_cleaner_run_level_backup_dry_run_lock_and_ontology(test_id: str) -> tuple[bool, list[Path]]:
    target_dir = PATHS.download_target / f"{test_id}_wikilink_target"
    backup_dir = PATHS.download_target / f"{test_id}_wikilink_backup"
    valid_note = target_dir / f"{test_id}_valid.md"
    source = target_dir / f"W {test_id} links.md"
    ontology = target_dir / f"{test_id}_ontology.md"
    moved_ontology = target_dir / "Ontology" / ontology.name
    dry_source = target_dir / f"W {test_id} dry.md"
    locked_source = target_dir / f"W {test_id} locked.md"
    limit_one = target_dir / f"W {test_id} limit one.md"
    limit_two = target_dir / f"W {test_id} limit two.md"

    cleanup = [
        valid_note,
        source,
        ontology,
        moved_ontology,
        dry_source,
        locked_source,
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
        f"Only [[{test_id}_missing_only]]\n"
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

    locked_source.write_text(f"Locked [[{test_id}_locked_missing]]\n", encoding="utf-8")
    locked_cleaner = WikilinkCleaner(
        str(target_dir),
        create_backup=False,
        file_lock_functions={
            "acquire": lambda _path: False,
            "release": lambda _path: None,
            "cleanup": lambda _path: None,
        },
    )
    locked_result = locked_cleaner.process_file(locked_source)
    locked_text = locked_source.read_text(encoding="utf-8")
    locked_stats = locked_cleaner.get_stats()

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
        and f"Dry [[{test_id}_dry_missing]]" in dry_text
        and dry_stats["broken_links_found"] == 1
        and dry_stats["broken_links_removed"] == 0
        and locked_result
        and f"Locked [[{test_id}_locked_missing]]" in locked_text
        and locked_stats["files_processed"] == 0
        and len(limited_files) == 1
    )

    print_result(
        "wikilink cleaner run level backup dry run lock and ontology",
        passed,
        {
            "run_stats": run_stats,
            "backups": len(backups),
            "dry_stats": dry_stats,
            "locked_stats": locked_stats,
            "limited_files": len(limited_files),
        },
    )

    return passed, cleanup


def test_utils_files_and_text_boundaries(test_id: str) -> tuple[bool, list[Path]]:
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


def test_start_system_creates_expected_threads_schedules_watchers_and_stop_clears_context(test_id: str) -> tuple[bool, list[Path]]:
    cleanup: list[Path] = []
    expected_threads = {
        "TTMLPipeline",
        "TextPipeline-Pretext",
        "TextPipeline-Extract",
        "TextPipeline-PremiumExtract",
        "AudioPipeline-GPU",
        "PeriodicScanner",
        "WikilinkCleaner",
        "XUrlDownloadPipeline",
    }
    expected_watch_paths = [
        str(CONFIG["PRETEXT_WATCH_FOLDER"]),
        str(CONFIG["EXTRACT_WATCH_FOLDER"]),
        str(CONFIG["PREMIUM_WATCH_FOLDER"]),
    ]

    class FakeObserver:
        def __init__(self):
            self.scheduled: list[tuple[object, str, bool]] = []
            self.started = False
            self.stopped = False
            self.joined = False

        def schedule(self, handler, path: str, recursive: bool = False) -> None:
            self.scheduled.append((handler, path, recursive))

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def join(self) -> None:
            self.joined = True

    def fake_worker(ctx, *_args) -> None:
        ctx.shutdown_flag.wait(5)

    original_values = {
        "process_ttml_pipeline": orchestrator_module.process_ttml_pipeline,
        "process_pretext_queue": orchestrator_module.process_pretext_queue,
        "process_extract_queue": orchestrator_module.process_extract_queue,
        "process_premium_extract_queue": orchestrator_module.process_premium_extract_queue,
        "process_audio_pipeline": orchestrator_module.process_audio_pipeline,
        "run_periodic_file_scanner": orchestrator_module.run_periodic_file_scanner,
        "process_wikilink_cleaning": orchestrator_module.process_wikilink_cleaning,
        "process_x_url_download_pipeline": orchestrator_module.process_x_url_download_pipeline,
        "file_scanner": orchestrator_module.file_scanner,
        "Observer": orchestrator_module.Observer,
        "read_prompt_file": orchestrator_module.read_prompt_file,
        "CURRENT_CONTEXT": orchestrator_module.CURRENT_CONTEXT,
    }

    handles = None

    try:
        orchestrator_module.process_ttml_pipeline = fake_worker
        orchestrator_module.process_pretext_queue = fake_worker
        orchestrator_module.process_extract_queue = fake_worker
        orchestrator_module.process_premium_extract_queue = fake_worker
        orchestrator_module.process_audio_pipeline = fake_worker
        orchestrator_module.run_periodic_file_scanner = fake_worker
        orchestrator_module.process_wikilink_cleaning = fake_worker
        orchestrator_module.process_x_url_download_pipeline = fake_worker
        orchestrator_module.file_scanner = lambda _ctx: None
        orchestrator_module.Observer = FakeObserver
        orchestrator_module.read_prompt_file = lambda filename: f"evaluation prompt {filename}"

        handles = orchestrator_module.start_system(CONFIG)
        thread_names = set(handles.threads)
        scheduled_paths = [path for _, path, _ in handles.observer.scheduled]
        scheduled_recursive = [recursive for _, _, recursive in handles.observer.scheduled]
        context_was_set = orchestrator_module.CURRENT_CONTEXT is handles.context
        status = orchestrator_module.system_status()

        orchestrator_module.stop_system(handles)
        for thread in handles.threads.values():
            thread.join(timeout=1)

        passed = (
            handles is not None
            and thread_names == expected_threads
            and handles.observer.started
            and handles.observer.stopped
            and handles.observer.joined
            and scheduled_paths == expected_watch_paths
            and scheduled_recursive == [False, False, False]
            and context_was_set
            and status["queues"] == {
                "pretext": 0,
                "extract": 0,
                "premium_extract": 0,
            }
            and handles.context.shutdown_flag.is_set()
            and orchestrator_module.CURRENT_CONTEXT is None
        )

        print_result(
            "start system creates expected threads schedules watchers and stop clears context",
            passed,
            {
                "thread_names": sorted(thread_names),
                "scheduled_paths": scheduled_paths,
                "context_was_set": context_was_set,
                "shutdown": handles.context.shutdown_flag.is_set(),
                "current_context": orchestrator_module.CURRENT_CONTEXT,
            },
        )

        return passed, cleanup

    finally:
        if handles is not None and not handles.context.shutdown_flag.is_set():
            orchestrator_module.stop_system(handles)
            for thread in handles.threads.values():
                thread.join(timeout=1)

        orchestrator_module.process_ttml_pipeline = original_values["process_ttml_pipeline"]
        orchestrator_module.process_pretext_queue = original_values["process_pretext_queue"]
        orchestrator_module.process_extract_queue = original_values["process_extract_queue"]
        orchestrator_module.process_premium_extract_queue = original_values["process_premium_extract_queue"]
        orchestrator_module.process_audio_pipeline = original_values["process_audio_pipeline"]
        orchestrator_module.run_periodic_file_scanner = original_values["run_periodic_file_scanner"]
        orchestrator_module.process_wikilink_cleaning = original_values["process_wikilink_cleaning"]
        orchestrator_module.process_x_url_download_pipeline = original_values["process_x_url_download_pipeline"]
        orchestrator_module.file_scanner = original_values["file_scanner"]
        orchestrator_module.Observer = original_values["Observer"]
        orchestrator_module.read_prompt_file = original_values["read_prompt_file"]
        orchestrator_module.CURRENT_CONTEXT = original_values["CURRENT_CONTEXT"]

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
            test_x_url_list_remove_completed,
            test_x_url_download_pipeline_mocked_loop_removes_completed_url,
            test_wikilink_cleaner_removes_broken_link,
            test_markdown_merge_updates_index,
            test_audio_move_to_done_removes_wav,
            test_pretext_request_deduplicates_queue,
            test_pretext_full_process_writes_pretext_markdown_and_archive,
            test_distill_collects_extract_outputs,
            test_extract_handler_queues_candidate_once,
            test_extract_full_process_writes_extract_markdown_and_archive,
            test_extract_failure_writes_error_and_moves_to_fail,
            test_premium_extract_full_process_archives_to_archive_folder,
            test_file_scanner_routes_text_inputs,
            test_periodic_file_scanner_routes_text_inputs,
            test_audio_scan_enqueues_audio_file,
            test_audio_process_file_mocked_full_path,
            test_process_queue_handles_lock_miss_errors_and_permanent_failures,
            test_distillation_success_skip_and_error_paths,
            test_extract_multi_model_partial_failure_preserves_success_outputs,
            test_pretext_multichunk_and_failure_release_request,
            test_audio_failure_paths_archive_or_cleanup,
            test_ttml_invalid_xml_restores_source_and_chinese_normalizes,
            test_x_url_failure_fallback_and_remove_failure_paths,
            test_wikilink_cleaner_run_level_backup_dry_run_lock_and_ontology,
            test_utils_files_and_text_boundaries,
            test_start_system_creates_expected_threads_schedules_watchers_and_stop_clears_context,
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
