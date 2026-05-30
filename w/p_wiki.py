"""
p_wiki.py

Responsibility
This module removes broken Obsidian-style wikilinks from selected Markdown notes
in the target directory.

Used by:
* p.py
* w/evaluation.py

Pipelines:
- wikilink worker -> clean dead links -> backup
- target_dir -> select_files -> process_file -> stats
- markdown -> find_wikilinks -> check_targets -> remove_links -> write
- markdown -> remove_lines -> preserve_spacing -> write
- file_path -> copy_backup -> write_file -> chmod

"""

import logging
from .helper_text import short_log_name
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from .helper_files import release_text_file_permissions
from .helper_ontology import move_ontology_instance_files


logger = logging.getLogger(__name__)
_cleaning_stats = {
    "files_processed": 0,
    "broken_links_found": 0,
    "broken_links_removed": 0,
    "files_modified": 0,
    "errors": 0,
    "last_run": None,
}


def get_cleaning_stats() -> Dict[str, Any]:
    """Return a snapshot of aggregated cleaning statistics."""
    return _cleaning_stats.copy()


def process_wikilink_cleaning(config, shutdown_flag, wikilink_cleaning_stats) -> None:
    intervals = config.get("INTERVALS", {})
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    while not shutdown_flag.is_set():
        try:
            clean_dead_links(
                target_dir=os.fspath(config["OBSIDIAN_SYNC_FOLDER"]),
                backup_dir=os.fspath(config["LINK_BACKUP_FOLDER"]),
                create_backup=True,
                dry_run=False,
                max_files=50,
            )
            wikilink_cleaning_stats["last_run"] = _cleaning_stats["last_run"]
            wikilink_cleaning_stats["cycle_count"] += 1

        except Exception:
            pass

        if shutdown_flag.wait(scan_seconds):
            return


def clean_dead_links(
    target_dir: str,
    backup_dir: str | None = None,
    create_backup: bool = True,
    dry_run: bool = False,
    max_files: int = 50,
) -> Dict[str, Any]:
    """Move ontology notes and clean broken wikilinks from selected Markdown files."""
    global _cleaning_stats
    run_stats = {
        "files_processed": 0,
        "broken_links_found": 0,
        "broken_links_removed": 0,
        "files_modified": 0,
        "errors": 0,
    }

    try:
        cleaner = WikilinkCleaner(
            target_dir,
            backup_dir,
            create_backup,
            dry_run,
            max_files,
        )
        cleaner.run_cleaning()

        run_stats = cleaner.get_stats()
        for key in run_stats:
            _cleaning_stats[key] += run_stats[key]
        _cleaning_stats["last_run"] = datetime.now()

        return run_stats

    except Exception as e:
        run_stats["errors"] += 1
        _cleaning_stats["errors"] += 1
        logger.error("WikilinkCleaner: Error during cleaning cycle: %s", str(e))
        return run_stats


class WikilinkCleaner:
    """Internal worker for broken wikilink cleanup."""

    def __init__(
        self,
        target_dir: str,
        backup_dir: str | None = None,
        create_backup: bool = True,
        dry_run: bool = False,
        max_files: int = 50,
    ):
        """Initialize cleaner paths, options, stats, and wikilink matching."""
        self.target_dir = Path(target_dir)
        self.backup_dir = (
            Path(backup_dir)
            if backup_dir
            else Path(target_dir).parent / "Archive" / "link_backup"
        )
        self.backup_enabled = create_backup
        self.dry_run = dry_run
        self.max_files = max_files
        self.logger = logger
        self.stats = {
            "files_processed": 0,
            "broken_links_found": 0,
            "broken_links_removed": 0,
            "files_modified": 0,
            "errors": 0,
        }

        self.wikilink_pattern = re.compile(r"(?<!\!)\[\[([^\]]+)\]\]")

        if self.backup_enabled:
            self.backup_dir.mkdir(parents=True, exist_ok=True)

    def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of stats for this cleaner instance."""
        return self.stats.copy()

    def find_target_files(self) -> List[Path]:
        """Find target Markdown files matching known Whisper naming patterns."""
        target_files: List[Path] = []

        if not self.target_dir.exists():
            self.logger.error(
                "WikilinkCleaner: Target directory does not exist: %s",
                short_log_name(self.target_dir),
            )
            return target_files

        search_specs = [
            ("Whisper 000000.md", "Found whisper.md file"),
            ("Whisper.md", "Found Whisper.md file"),
            ("W *.md", "Found W pattern file"),
        ]

        for pattern, msg in search_specs:
            for file_path in self.target_dir.glob(pattern):
                if file_path.is_file():
                    target_files.append(file_path)
                    self.logger.debug(
                        "WikilinkCleaner: %s: %s", msg, short_log_name(file_path.name)
                    )

        if len(target_files) > self.max_files:
            target_files = target_files[: self.max_files]
            self.logger.info(
                "WikilinkCleaner: Limited to %d files per cleaning cycle",
                self.max_files,
            )

        self.logger.debug(
            "WikilinkCleaner: Found %d target markdown files to process",
            len(target_files),
        )
        return target_files

    def get_existing_files(self, directory: Path) -> Set[str]:
        """Return note names that exist in `directory` with and without `.md`."""
        existing_files: Set[str] = set()

        for file_path in directory.glob("*.md"):
            if file_path.is_file():
                filename_without_ext = file_path.stem
                filename_with_ext = file_path.name
                existing_files.add(filename_without_ext)
                existing_files.add(filename_with_ext)

        return existing_files

    def extract_wikilinks(self, content: str) -> List[Tuple[str, str]]:
        """Extract non-embedded wikilinks from Markdown content."""
        wikilinks: List[Tuple[str, str]] = []
        for match in self.wikilink_pattern.finditer(content):
            full_match = match.group(0)
            filename = match.group(1).strip()
            wikilinks.append((full_match, filename))
        return wikilinks


    def is_link_broken(self, filename: str, existing_files: Set[str]) -> bool:
        """Return whether a wikilink target is missing from known note names."""
        return filename not in existing_files and (
            filename.endswith(".md") or f"{filename}.md" not in existing_files
        )

    def line_has_active_wikilink(self, line: str, existing_files: Set[str]) -> bool:
        """Return whether a line contains at least one non-broken wikilink."""
        return any(
            not self.is_link_broken(filename, existing_files)
            for _, filename in self.extract_wikilinks(line)
        )

    def create_backup(self, file_path: Path) -> bool:
        """Copy a file to the backup directory when backups are enabled."""
        if not self.backup_enabled:
            return True

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{file_path.stem}_backup_{timestamp}{file_path.suffix}"
            backup_path = self.backup_dir / backup_name

            shutil.copy2(file_path, backup_path)
            release_text_file_permissions(backup_path)
            self.logger.debug(
                "WikilinkCleaner: Created backup: %s", backup_path
            )
            return True
        except Exception as e:
            self.logger.error(
                "WikilinkCleaner: Failed to create backup for %s: %s",
                file_path,
                e,
            )
            self.stats["errors"] += 1
            return False

    def process_file(self, file_path: Path) -> bool:
        """Remove broken wikilinks and adjacent empty lines from one Markdown file."""
        try:
            self.logger.debug(
                "WikilinkCleaner: Processing file: %s", file_path
            )

            with open(file_path, "r", encoding="utf-8") as f:
                original_content = f.read()

            existing_files = self.get_existing_files(file_path.parent)

            if not self.wikilink_pattern.search(original_content):
                self.logger.debug(
                    "WikilinkCleaner: No wikilinks found in %s", file_path
                )
                return True

            lines = original_content.split("\n")
            broken_links_in_file = 0
            empty_lines_removed = 0
            remove_flags = [False] * len(lines)

            for i, line in enumerate(lines):
                modified_line = line
                for full_match, filename in self.extract_wikilinks(line):
                    if self.is_link_broken(filename, existing_files):
                        self.logger.debug(
                            "WikilinkCleaner: Found broken wikilink: %s -> %s",
                            full_match,
                            filename,
                        )
                        self.stats["broken_links_found"] += 1
                        broken_links_in_file += 1
                        modified_line = modified_line.replace(full_match, "")
                        if not self.dry_run:
                            self.stats["broken_links_removed"] += 1
                            self.logger.debug(
                                "WikilinkCleaner: Removed broken wikilink: %s",
                                full_match,
                            )
                if modified_line != line:
                    if modified_line.strip() == "":
                        remove_flags[i] = True
                        self.logger.debug(
                            "WikilinkCleaner: Marked line %d for removal (contained only broken wikilinks)",
                            i + 1,
                        )
                    else:
                        self.logger.debug(
                            "WikilinkCleaner: Line %d has other content besides broken wikilinks, keeping it",
                            i + 1,
                        )
                    if not self.dry_run:
                        lines[i] = modified_line

            for i, line in enumerate(lines):
                if line.strip() or remove_flags[i]:
                    continue
                left_removed = i > 0 and remove_flags[i - 1]
                right_removed = i + 1 < len(lines) and remove_flags[i + 1]
                left_active = i > 0 and self.line_has_active_wikilink(lines[i - 1], existing_files)
                right_active = i + 1 < len(lines) and self.line_has_active_wikilink(lines[i + 1], existing_files)
                if not ((left_active and not left_removed) or (right_active and not right_removed)):
                    remove_flags[i] = True
                    empty_lines_removed += 1
                    self.logger.debug(
                        "WikilinkCleaner: Marked adjacent empty line %d for removal (%s)",
                        i + 1,
                        (
                            f"after removed line {i}"
                            if i == len(lines) - 1
                            or not remove_flags[i + 1]
                            else f"between removed lines {i} and {i + 2}"
                        ),
                    )
                else:
                    self.logger.debug(
                        "WikilinkCleaner: Preserving empty line %d - needed spacing between active wikilinks",
                        i + 1,
                    )

            modified_lines = [line for line, remove in zip(lines, remove_flags) if not remove]

            if broken_links_in_file > 0:
                if self.dry_run:
                    self.logger.info(
                        "WikilinkCleaner: DRY RUN - Would remove %d broken links%s from %s",
                        broken_links_in_file,
                        f" and {empty_lines_removed} adjacent empty lines"
                        if empty_lines_removed > 0
                        else "",
                        file_path.name,
                    )
                else:
                    modified_content = "\n".join(modified_lines)

                    if not self.create_backup(file_path):
                        self.logger.error(
                            "WikilinkCleaner: Skipping file due to backup failure: %s",
                            file_path,
                        )
                        return False

                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(modified_content)
                    release_text_file_permissions(file_path)

                    self.stats["files_modified"] += 1
                    self.logger.info(
                        "%s (%d broken%s)",
                        file_path.name,
                        broken_links_in_file,
                        f", {empty_lines_removed} empty"
                        if empty_lines_removed > 0
                        else "",
                    )

            self.stats["files_processed"] += 1
            return True

        except Exception as e:
            self.logger.error(
                "WikilinkCleaner: Error processing file %s: %s",
                short_log_name(file_path),
                e,
            )
            self.stats["errors"] += 1
            return False

    def run_cleaning(self) -> None:
        """Run ontology moves before processing selected Markdown notes."""
        self.stats["errors"] += move_ontology_instance_files(
            self.target_dir, self.create_backup, self.dry_run, self.logger
        )
        target_files = self.find_target_files()

        if not target_files:
            self.logger.debug(
                "WikilinkCleaner: No target markdown files found to process"
            )
            return

        for file_path in target_files:
            self.process_file(file_path)
