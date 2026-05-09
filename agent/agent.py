# python agent/agent.py agent/task.md
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helper.helper_llm import call_llm  # noqa: E402


POS_FILES = (
    "AGENTS.md",
    "context.md",
    "decisions.md",
    "proposals.md",
    "assets.md",
)

DEFAULT_MODEL = "gpt-5.4-mini"
PROMPT_PATH = REPO_ROOT / "prompt" / "agent_repo_plan.txt"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_optional(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def parse_allowed_files(task_text: str) -> list[str]:
    match = re.search(r"(?im)^##\s*Allowed files\s*$([\s\S]*?)(?=^##\s|\Z)", task_text)
    if not match:
        return []

    files: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            files.append(line[2:].strip())
    return files


def load_pos_context() -> str:
    parts: list[str] = []
    for name in POS_FILES:
        path = REPO_ROOT / "pos" / name
        text = read_optional(path).strip()
        if text:
            parts.append(f"# pos/{name}\n\n{text}")
    return "\n\n---\n\n".join(parts)


def load_allowed_file_context(file_paths: list[str]) -> str:
    parts: list[str] = []
    for rel in file_paths:
        path = REPO_ROOT / rel
        if not path.exists():
            parts.append(f"# {rel}\n\n<FILE NOT FOUND>")
            continue
        parts.append(f"# {rel}\n\n```text\n{read_text(path)}\n```")
    return "\n\n---\n\n".join(parts)


def build_prompt(task_text: str, pos_context: str, file_context: str) -> str:
    template = read_text(PROMPT_PATH)
    return template.format(
        task_text=task_text,
        pos_context=pos_context,
        file_context=file_context,
    )


def main() -> None:
    task_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("task.md")
    if not task_path.is_absolute():
        task_path = Path.cwd() / task_path

    task_text = read_text(task_path)
    allowed_files = parse_allowed_files(task_text)
    pos_context = load_pos_context()
    file_context = load_allowed_file_context(allowed_files)

    print("=== Repo Planning Agent ===")
    print(f"task: {task_path}")
    print(f"prompt: {PROMPT_PATH}")
    print(f"model: {DEFAULT_MODEL}")
    print("allowed files:")
    for file_path in allowed_files or ["<none>"]:
        print(f"  - {file_path}")
    print()

    output = call_llm(
        DEFAULT_MODEL,
        system_prompt="You are a strict minimal-change repo planning agent.",
        user_text=build_prompt(task_text, pos_context, file_context),
        file_path=str(task_path),
        max_retries=2,
        timeout=120,
    )

    print(output)


if __name__ == "__main__":
    main()