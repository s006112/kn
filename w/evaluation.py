from __future__ import annotations

import sys
import uuid
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
W_DIR = Path(__file__).resolve().parent

for path in (ROOT_DIR, W_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from p import CONFIG  # noqa: E402
from p_torrent import scan_torrent_watch_folder  # noqa: E402


OK = "✅"
FAIL = "❌"


def safe_delete(path: Path, test_id: str) -> None:
    if path.exists() and path.is_file() and test_id in path.name:
        path.unlink()


def main() -> int:
    test_id = f"EVAL_{uuid.uuid4().hex[:8]}"
    filename = f"{test_id}.torrent"

    watch_folder = Path(CONFIG["WATCH_FOLDER"])
    whisper_folder = Path(CONFIG["WHISPER_FOLDER"])

    source = watch_folder / filename
    target = whisper_folder / filename

    created = []
    cleanup = []

    try:
        watch_folder.mkdir(parents=True, exist_ok=True)
        whisper_folder.mkdir(parents=True, exist_ok=True)

        source.write_text(f"dummy torrent test {test_id}\n", encoding="utf-8")
        time.sleep(2)
        created.append(source)
        cleanup.extend([source, target])

        moved_count = scan_torrent_watch_folder(CONFIG)

        passed = (
            moved_count >= 1
            and not source.exists()
            and target.exists()
            and target.is_file()
        )

        print(f"{OK if passed else FAIL} torrent move")
        print(f"  source: {source}")
        print(f"  target: {target}")
        print(f"  moved_count: {moved_count}")

        return 0 if passed else 1

    except Exception as exc:
        print(f"{FAIL} torrent move")
        print(f"  error: {exc}")
        return 1

    finally:
        for path in cleanup:
            safe_delete(path, test_id)


if __name__ == "__main__":
    raise SystemExit(main())