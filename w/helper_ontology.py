"""
helper_ontology.py

Responsibility
Move ontology instance Markdown files into the ontology subdirectory.

Used by:
* w/p_wiki.py

Pipeline:
- target_dir -> detect_ontology -> backup -> move_ontology

"""

import logging
import shutil
from pathlib import Path
from typing import Callable

from .helper_files import release_text_file_permissions


def move_ontology_instance_files(
    target_dir: Path,
    create_backup: Callable[[Path], bool],
    dry_run: bool,
    logger: logging.Logger,
) -> int:
    """Move ontology instance Markdown files and return the number of errors."""
    errors = 0
    ontology_dir = target_dir / "Ontology"

    for file_path in target_dir.glob("*.md"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                original_content = f.read()

            if not original_content.startswith("Class::"):
                continue

            destination_path = ontology_dir / file_path.name

            if dry_run:
                logger.info(
                    "WikilinkCleaner: DRY RUN - Would move ontology file %s to %s",
                    file_path.name,
                    destination_path,
                )
                continue

            ontology_dir.mkdir(parents=True, exist_ok=True)

            if not create_backup(file_path):
                logger.error(
                    "WikilinkCleaner: Skipping ontology move due to backup failure: %s",
                    file_path,
                )
                continue

            shutil.move(str(file_path), str(destination_path))
            release_text_file_permissions(destination_path)
            logger.info(
                "WikilinkCleaner: Moved ontology file %s to %s",
                file_path.name,
                destination_path,
            )

        except Exception as e:
            logger.error(
                "WikilinkCleaner: Error moving ontology file %s: %s",
                file_path,
                e,
            )
            errors += 1

    return errors
