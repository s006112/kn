import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

from utils_files import release_text_file_permissions


_shared_logger = None
_cleaning_stats = {
    "files_processed": 0,
    "broken_links_found": 0,
    "broken_links_removed": 0,
    "files_modified": 0,
    "last_run": None,
    "errors": 0,
}


def setup_wikilink_cleaner_logging(existing_logger: logging.Logger) -> None:
    """Configure logging to use external logger instance."""
    global _shared_logger
    _shared_logger = existing_logger


def get_cleaning_stats() -> Dict[str, any]:
    """Return cleaning statistics for status reporting."""
    return _cleaning_stats.copy()


def clean_dead_links(
    target_dir: str,
    backup_dir: str | None = None,
    create_backup: bool = True,
    dry_run: bool = False,
    max_files: int = 50,
    file_lock_functions: Dict | None = None,
) -> Dict[str, any]:
    """
    Main cleaning function for integration with pipeline or CLI.
    """
    global _cleaning_stats, _shared_logger

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
            file_lock_functions,
        )
        cleaner.run_cleaning()

        run_stats = cleaner.get_stats()
        _cleaning_stats["files_processed"] += run_stats["files_processed"]
        _cleaning_stats["broken_links_found"] += run_stats["broken_links_found"]
        _cleaning_stats["broken_links_removed"] += run_stats["broken_links_removed"]
        _cleaning_stats["files_modified"] += run_stats["files_modified"]
        _cleaning_stats["errors"] += run_stats["errors"]
        _cleaning_stats["last_run"] = datetime.now()

        return run_stats

    except Exception as e:
        run_stats["errors"] += 1
        _cleaning_stats["errors"] += 1
        if _shared_logger:
            _shared_logger.error(
                "WikilinkCleaner: Error during cleaning cycle: %s", str(e)
            )
        return run_stats


class WikilinkCleaner:
    """Internal class for cleaning broken wikilinks in Obsidian markdown files."""

    def __init__(
        self,
        target_dir: str,
        backup_dir: str | None = None,
        create_backup: bool = True,
        dry_run: bool = False,
        max_files: int = 50,
        file_lock_functions: Dict | None = None,
    ):
        self.target_dir = Path(target_dir)
        self.backup_dir = (
            Path(backup_dir)
            if backup_dir
            else Path(target_dir).parent / "Archive" / "link_backup"
        )
        self.backup_enabled = create_backup
        self.dry_run = dry_run
        self.max_files = max_files
        self.logger = _shared_logger
        self.file_lock_functions = file_lock_functions or {}
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

    def get_stats(self) -> Dict[str, any]:
        return self.stats.copy()

    def find_target_files(self) -> List[Path]:
        target_files: List[Path] = []

        if not self.target_dir.exists():
            if self.logger:
                self.logger.error(
                    "WikilinkCleaner: Target directory does not exist: %s",
                    self.target_dir,
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
                    if self.logger:
                        self.logger.debug(
                            "WikilinkCleaner: %s: %s", msg, file_path.name
                        )

        if len(target_files) > self.max_files:
            target_files = target_files[: self.max_files]
            if self.logger:
                self.logger.info(
                    "WikilinkCleaner: Limited to %d files per cleaning cycle",
                    self.max_files,
                )

        if self.logger:
            self.logger.debug(
                "WikilinkCleaner: Found %d target markdown files to process",
                len(target_files),
            )
        return target_files

    def get_existing_files(self, directory: Path) -> Set[str]:
        existing_files: Set[str] = set()

        for file_path in directory.glob("*.md"):
            if file_path.is_file():
                filename_without_ext = file_path.stem
                filename_with_ext = file_path.name
                existing_files.add(filename_without_ext)
                existing_files.add(filename_with_ext)

        return existing_files

    def extract_wikilinks(self, content: str) -> List[Tuple[str, str]]:
        wikilinks: List[Tuple[str, str]] = []
        for match in self.wikilink_pattern.finditer(content):
            full_match = match.group(0)
            filename = match.group(1).strip()
            wikilinks.append((full_match, filename))
        return wikilinks

    def is_link_broken(self, filename: str, existing_files: Set[str]) -> bool:
        return filename not in existing_files and (
            filename.endswith(".md") or f"{filename}.md" not in existing_files
        )

    def _has_active_wikilink(self, line: str, existing_files: Set[str]) -> bool:
        line_wikilinks = self.extract_wikilinks(line)
        if not line_wikilinks:
            return False
        return any(
            not self.is_link_broken(filename, existing_files)
            for _, filename in line_wikilinks
        )

    def create_backup(self, file_path: Path) -> bool:
        if not self.backup_enabled:
            return True

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{file_path.stem}_backup_{timestamp}{file_path.suffix}"
            backup_path = self.backup_dir / backup_name

            shutil.copy2(file_path, backup_path)
            if self.logger:
                self.logger.debug(
                    "WikilinkCleaner: Created backup: %s", backup_path
                )
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(
                    "WikilinkCleaner: Failed to create backup for %s: %s",
                    file_path,
                    e,
                )
            self.stats["errors"] += 1
            return False

    def _acquire_lock(self, file_path_str: str) -> bool:
        acquire = (
            self.file_lock_functions.get("acquire")
            if self.file_lock_functions
            else None
        )
        if not acquire:
            return False
        try:
            return bool(acquire(file_path_str))
        except Exception:
            return False

    def _release_lock(self, file_path_str: str, lock_acquired: bool) -> None:
        if not (lock_acquired and self.file_lock_functions):
            return
        release = self.file_lock_functions.get("release")
        cleanup = self.file_lock_functions.get("cleanup")
        try:
            if release:
                release(file_path_str)
            if cleanup:
                cleanup(file_path_str)
        except Exception as e:
            if self.logger:
                self.logger.debug(
                    "WikilinkCleaner: Error releasing lock for %s: %s",
                    Path(file_path_str).name,
                    e,
                )

    def process_file(self, file_path: Path) -> bool:
        file_path_str = str(file_path)
        lock_acquired = self._acquire_lock(file_path_str)
        if self.file_lock_functions and not lock_acquired:
            return True

        try:
            if self.logger:
                self.logger.debug(
                    "WikilinkCleaner: Processing file: %s", file_path
                )

            with open(file_path, "r", encoding="utf-8") as f:
                original_content = f.read()

            existing_files = self.get_existing_files(file_path.parent)

            if not self.wikilink_pattern.search(original_content):
                if self.logger:
                    self.logger.debug(
                        "WikilinkCleaner: No wikilinks found in %s", file_path
                    )
                self.stats["files_processed"] += 1
                return True

            lines = original_content.split("\n")
            broken_links_in_file = 0
            removed_line_indices: Set[int] = set()

            for i, line in enumerate(lines):
                line_wikilinks = self.extract_wikilinks(line)
                line_modified = line
                line_had_broken_links = False

                for full_match, filename in line_wikilinks:
                    if self.is_link_broken(filename, existing_files):
                        if self.logger:
                            self.logger.debug(
                                "WikilinkCleaner: Found broken wikilink: %s -> %s",
                                full_match,
                                filename,
                            )
                        self.stats["broken_links_found"] += 1
                        broken_links_in_file += 1
                        line_had_broken_links = True
                        line_modified = line_modified.replace(full_match, "")

                        if not self.dry_run:
                            self.stats["broken_links_removed"] += 1
                            if self.logger:
                                self.logger.debug(
                                    "WikilinkCleaner: Removed broken wikilink: %s",
                                    full_match,
                                )

                if line_had_broken_links:
                    if line_modified.strip() == "":
                        removed_line_indices.add(i)
                        if self.logger:
                            self.logger.debug(
                                "WikilinkCleaner: Marked line %d for removal (contained only broken wikilinks)",
                                i + 1,
                            )
                    else:
                        if self.logger:
                            self.logger.debug(
                                "WikilinkCleaner: Line %d has other content besides broken wikilinks, keeping it",
                                i + 1,
                            )

                if (
                    line_had_broken_links
                    and not self.dry_run
                    and line_modified.strip() != ""
                ):
                    lines[i] = line_modified

            adjacent_empty_lines_to_remove: Set[int] = set()

            for i, line in enumerate(lines):
                if i in removed_line_indices:
                    continue
                if line.strip() == "":
                    is_adjacent_to_removed = False
                    adjacent_reason = ""

                    if i > 0 and (i - 1) in removed_line_indices:
                        is_adjacent_to_removed = True
                        adjacent_reason = f"after removed line {i}"

                    if i < len(lines) - 1 and (i + 1) in removed_line_indices:
                        is_adjacent_to_removed = True
                        if adjacent_reason:
                            adjacent_reason = (
                                f"between removed lines {i} and {i + 2}"
                            )
                        else:
                            adjacent_reason = (
                                f"before removed line {i + 2}"
                            )

                    if is_adjacent_to_removed:
                        should_preserve = False

                        prev_has_active = (
                            i > 0
                            and (i - 1) not in removed_line_indices
                            and self._has_active_wikilink(
                                lines[i - 1], existing_files
                            )
                        )
                        next_has_active = (
                            i < len(lines) - 1
                            and (i + 1) not in removed_line_indices
                            and self._has_active_wikilink(
                                lines[i + 1], existing_files
                            )
                        )

                        if prev_has_active and next_has_active:
                            should_preserve = True
                            if self.logger:
                                self.logger.debug(
                                    "WikilinkCleaner: Preserving empty line %d - needed spacing between active wikilinks",
                                    i + 1,
                                )

                        if not should_preserve:
                            adjacent_empty_lines_to_remove.add(i)
                            if self.logger:
                                self.logger.debug(
                                    "WikilinkCleaner: Marked adjacent empty line %d for removal (%s)",
                                    i + 1,
                                    adjacent_reason,
                                )

            all_removed_indices = removed_line_indices.union(
                adjacent_empty_lines_to_remove
            )
            modified_lines = [
                line
                for i, line in enumerate(lines)
                if i not in all_removed_indices
            ]

            if broken_links_in_file > 0 and not self.dry_run:
                modified_content = "\n".join(modified_lines)

                if not self.create_backup(file_path):
                    if self.logger:
                        self.logger.error(
                            "WikilinkCleaner: Skipping file due to backup failure: %s",
                            file_path,
                        )
                    return False

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(modified_content)
                release_text_file_permissions(file_path)

                self.stats["files_modified"] += 1
                if self.logger:
                    empty_lines_removed = len(adjacent_empty_lines_to_remove)
                    extra = (
                        f", {empty_lines_removed} empty"
                        if empty_lines_removed > 0
                        else ""
                    )
                    self.logger.info(
                        "%s (%d broken%s)",
                        file_path.name,
                        broken_links_in_file,
                        extra,
                    )

            elif broken_links_in_file > 0 and self.dry_run:
                if self.logger:
                    n = len(adjacent_empty_lines_to_remove)
                    extra = (
                        f" and {n} adjacent empty lines" if n > 0 else ""
                    )
                    self.logger.info(
                        "WikilinkCleaner: DRY RUN - Would remove %d broken links%s from %s",
                        broken_links_in_file,
                        extra,
                        file_path.name,
                    )

            self.stats["files_processed"] += 1
            return True

        except Exception as e:
            if self.logger:
                self.logger.error(
                    "WikilinkCleaner: Error processing file %s: %s",
                    file_path,
                    e,
                )
            self.stats["errors"] += 1
            return False
        finally:
            self._release_lock(file_path_str, lock_acquired)

    def run_cleaning(self) -> bool:
        target_files = self.find_target_files()

        if not target_files:
            if self.logger:
                self.logger.debug(
                    "WikilinkCleaner: No target markdown files found to process"
                )
            return True

        success = True
        for file_path in target_files:
            success &= bool(self.process_file(file_path))
        return success
