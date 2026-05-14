'''
python agent/agent.py agent/task.md --draft-task w/p_ytd.py     # step 0
python agent/agent.py agent/task.md  # step 1
python agent/agent.py agent/task.md --review-last  # step 2
python agent/agent.py agent/task.md --revise-last  # step 3
python agent/agent.py agent/task.md --status
python agent/agent.py agent/task.md --show-last
python agent/agent.py agent/task.md --accept-last  # step 4
python agent/agent.py agent/task.md --clear-trace
python agent/agent.py agent/task.md --show-final
python agent/agent.py agent/task.md --check-ready
python agent/agent.py agent/task.md --show-commands
python agent/agent.py agent/task.md --make-patch  # step 5
python agent/agent.py agent/task.md --check-patch  # step 6
python agent/agent.py agent/task.md --apply-patch  # step 7
python agent/agent.py agent/task.md --run-verify  # step 8

Workflow: plan -> review -> revise -> accept -> make patch -> check patch -> apply patch -> run verify
'''

from __future__ import annotations

import re
import subprocess
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

WORKFLOW_FILES = (
    "agent.md",
    "workflow.md",
    "agent/agent.md",
    "agent/workflow.md",
)

DEFAULT_MODEL = "gpt-5.4-mini"
S0_TASK_PROMPT = REPO_ROOT / "agent" / "agent_s0_task.txt"
S1_PLAN_PROMPT = REPO_ROOT / "agent" / "agent_s1_plan.txt"
S2_REVIEW_PROMPT = REPO_ROOT / "agent" / "agent_s2_review.txt"
S3_REVISE_PROMPT = REPO_ROOT / "agent" / "agent_s3_revise.txt"
S5_PATCH_PROMPT = REPO_ROOT / "agent" / "agent_s5_patch.txt"

LAST_PATCH_PATH = REPO_ROOT / "agent" / "last_patch.txt"
LAST_PROMPT_PATH = REPO_ROOT / "agent" / "last_prompt.md"
LAST_PLAN_PATH = REPO_ROOT / "agent" / "last_plan.md"
LAST_REVIEW_PATH = REPO_ROOT / "agent" / "last_review.md"
LAST_REVISED_PLAN_PATH = REPO_ROOT / "agent" / "last_revised_plan.md"
FINAL_PLAN_PATH = REPO_ROOT / "agent" / "final_plan.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_optional(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def flag_value(flag: str) -> str | None:
    if flag not in sys.argv:
        return None
    index = sys.argv.index(flag)
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


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


def load_workflow_context() -> str:
    parts: list[str] = []
    for name in WORKFLOW_FILES:
        path = REPO_ROOT / name
        text = read_optional(path).strip()
        if text:
            parts.append(f"# {name}\n\n{text}")
    return "\n\n---\n\n".join(parts)


def load_single_file_context(path: Path) -> str:
    rel = repo_rel(path)
    if not path.exists():
        return f"# {rel}\n\n<FILE NOT FOUND>"
    return f"# {rel}\n\n```text\n{read_text(path)}\n```"


def load_allowed_file_context(file_paths: list[str]) -> str:
    parts: list[str] = []
    for rel in file_paths:
        path = REPO_ROOT / rel
        if not path.exists():
            parts.append(f"# {rel}\n\n<FILE NOT FOUND>")
            continue
        parts.append(f"# {rel}\n\n```text\n{read_text(path)}\n```")
    return "\n\n---\n\n".join(parts)


def build_task_prompt(target_path: str, pos_context: str, workflow_context: str, file_context: str) -> str:
    # Step 0: draft task.
    template = read_text(S0_TASK_PROMPT)
    return template.format(target_path=target_path, pos_context=pos_context, workflow_context=workflow_context, file_context=file_context)


def build_plan_prompt(task_text: str, pos_context: str, file_context: str) -> str:
    # Step 1: plan.
    template = read_text(S1_PLAN_PROMPT)
    return template.format(task_text=task_text, pos_context=pos_context, file_context=file_context)


def build_review_prompt(task_text: str, plan_text: str) -> str:
    # Step 2: review.
    template = read_text(S2_REVIEW_PROMPT)
    return template.format(task_text=task_text, plan_text=plan_text)


def build_revise_prompt(task_text: str, plan_text: str, review_text: str) -> str:
    # Step 3: revise.
    template = read_text(S3_REVISE_PROMPT)
    return template.format(task_text=task_text, plan_text=plan_text, review_text=review_text)


def print_status(task_path: Path) -> None:
    paths = {
        "task": task_path,
        "last_prompt": LAST_PROMPT_PATH,
        "last_plan": LAST_PLAN_PATH,
        "last_review": LAST_REVIEW_PATH,
        "last_revised_plan": LAST_REVISED_PLAN_PATH,
    }

    print("=== Agent Trace Status ===")
    for name, path in paths.items():
        status = "exists" if path.exists() else "missing"
        print(f"{name}: {status} - {path}")


def show_last_plan() -> None:
    path = LAST_REVISED_PLAN_PATH if LAST_REVISED_PLAN_PATH.exists() else LAST_PLAN_PATH
    if not path.exists():
        print("No plan found.")
        return

    print("=== Last Agent Plan ===")
    print(f"source: {path}\n")
    print(read_text(path))


def accept_last_plan() -> None:
    # Step 4: accept.
    source = LAST_REVISED_PLAN_PATH if LAST_REVISED_PLAN_PATH.exists() else LAST_PLAN_PATH
    if not source.exists():
        print("No plan found to accept.")
        return

    content = read_text(source)
    FINAL_PLAN_PATH.write_text(f"# Final Accepted Plan\n\nSource: `{source}`\n\n---\n\n{content}", encoding="utf-8")
    print(f"Accepted plan: {FINAL_PLAN_PATH}")


def clear_trace() -> None:
    paths = (
        LAST_PROMPT_PATH,
        LAST_PLAN_PATH,
        LAST_REVIEW_PATH,
        LAST_REVISED_PLAN_PATH,
    )

    for path in paths:
        if path.exists():
            path.unlink()
            print(f"removed: {path}")
        else:
            print(f"missing: {path}")

    print("Trace cleared. final_plan.md preserved.")


def show_final_plan() -> None:
    if not FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return

    print("=== Final Accepted Plan ===")
    print(f"source: {FINAL_PLAN_PATH}\n")
    print(read_text(FINAL_PLAN_PATH))


def check_ready(task_path: Path) -> None:
    checks = []

    checks.append(("task exists", task_path.exists()))
    checks.append(("final_plan exists", FINAL_PLAN_PATH.exists()))

    task_text = read_text(task_path) if task_path.exists() else ""
    allowed_files = parse_allowed_files(task_text) if task_text else []

    checks.append(("allowed files declared", bool(allowed_files)))

    for rel in allowed_files:
        checks.append((f"allowed file exists: {rel}", (REPO_ROOT / rel).exists()))

    final_text = read_text(FINAL_PLAN_PATH) if FINAL_PLAN_PATH.exists() else ""
    checks.append(("final plan has evaluation command", "Evaluation command" in final_text or "evaluation" in final_text.lower()))
    checks.append(("final plan has stop condition", "Stop condition" in final_text or "停止条件" in final_text))

    print("=== Agent Ready Check ===")
    ok = True
    for name, passed in checks:
        mark = "OK" if passed else "MISSING"
        print(f"{mark}: {name}")
        ok = ok and passed

    print()
    print("READY" if ok else "NOT READY")


def get_final_plan_commands() -> list[str]:
    text = read_text(FINAL_PLAN_PATH)
    commands: list[str] = []
    in_bash = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_bash = stripped in ("```bash", "```sh", "```shell")
            continue
        if in_bash and stripped:
            commands.append(stripped)

    return commands


def show_commands() -> None:
    if not FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return

    commands = get_final_plan_commands()

    print("=== Final Plan Commands ===")
    if not commands:
        print("No commands found.")
        return

    for command in commands:
        print(command)


def run_verify() -> None:
    # Step 8: run verify.
    if not FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return

    commands = get_final_plan_commands()

    print("=== Verify Patch Commands ===", flush=True)
    if not commands:
        print("No commands found.", flush=True)
        return

    for command in commands:
        print(f"$ {command}", flush=True)
        result = subprocess.run(command, cwd=REPO_ROOT, shell=True)
        if result.returncode != 0:
            print(f"VERIFY_FAILED: {command}", flush=True)
            sys.exit(result.returncode)

    print("VERIFY_OK", flush=True)


def build_patch_prompt(task_text: str, final_plan_text: str, allowed_files: list[str], file_context: str) -> str:
    # Step 5: make patch.
    template = read_text(S5_PATCH_PROMPT)
    return template.format(task_text=task_text, final_plan_text=final_plan_text, allowed_files_text="\n".join(f"- {p}" for p in allowed_files), file_context=file_context)


def parse_patch_blocks(patch_text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    lines = patch_text.splitlines()
    blocks: list[tuple[str, str, str]] = []
    errors: list[str] = []
    index = 0

    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue

        if not lines[index].startswith("FILE: "):
            errors.append(f"expected FILE at line {index + 1}")
            break

        rel = lines[index][len("FILE: "):].strip()
        index += 1

        if index >= len(lines) or lines[index] != "SEARCH:":
            errors.append(f"expected SEARCH after FILE: {rel}")
            break
        index += 1

        search_lines: list[str] = []
        while index < len(lines) and lines[index] != "REPLACE:":
            search_lines.append(lines[index])
            index += 1

        if index >= len(lines):
            errors.append(f"missing REPLACE for FILE: {rel}")
            break
        index += 1

        replace_lines: list[str] = []
        while index < len(lines) and lines[index] != "END":
            replace_lines.append(lines[index])
            index += 1

        if index >= len(lines):
            errors.append(f"missing END for FILE: {rel}")
            break
        index += 1

        blocks.append((rel, "\n".join(search_lines), "\n".join(replace_lines)))

    if not blocks:
        errors.append("no patch blocks found")

    return blocks, errors


def validate_patch_blocks(task_path: Path, blocks: list[tuple[str, str, str]]) -> list[str]:
    task_text = read_text(task_path) if task_path.exists() else ""
    allowed_files = set(parse_allowed_files(task_text))
    errors: list[str] = []

    for rel, search, _replace in blocks:
        path = REPO_ROOT / rel

        if rel not in allowed_files:
            errors.append(f"file not allowed: {rel}")
            continue

        if not path.exists():
            errors.append(f"file not found: {rel}")
            continue

        if not search:
            errors.append(f"empty SEARCH block: {rel}")
            continue

        count = read_text(path).count(search)
        if count != 1:
            errors.append(f"SEARCH match count for {rel}: {count}")

    return errors


def check_patch(task_path: Path) -> None:
    # Step 6: check patch.
    if not LAST_PATCH_PATH.exists():
        print("No patch found.")
        return

    patch_text = read_text(LAST_PATCH_PATH)
    if patch_text.strip() == "PATCH_NOT_SAFE":
        print("PATCH_NOT_SAFE")
        return

    blocks, errors = parse_patch_blocks(patch_text)
    errors.extend(validate_patch_blocks(task_path, blocks))

    if errors:
        print("PATCH_INVALID")
        for error in errors:
            print(error)
        return

    print("PATCH_OK")


def apply_patch(task_path: Path) -> None:
    # Step 7: apply patch.
    if not LAST_PATCH_PATH.exists():
        print("No patch found.")
        return

    patch_text = read_text(LAST_PATCH_PATH)
    if patch_text.strip() == "PATCH_NOT_SAFE":
        print("PATCH_NOT_SAFE")
        return

    blocks, errors = parse_patch_blocks(patch_text)
    errors.extend(validate_patch_blocks(task_path, blocks))

    if errors:
        print("PATCH_INVALID")
        for error in errors:
            print(error)
        return

    for rel, search, replace in blocks:
        path = REPO_ROOT / rel
        text = read_text(path)
        path.write_text(text.replace(search, replace, 1), encoding="utf-8")

    print("PATCH_APPLIED")
    print("Next: python agent/agent.py agent/task.md --run-verify")


def draft_task(task_path: Path, target_arg: str) -> None:
    # Step 0: draft task.md from one target source file.
    target_path = Path(target_arg)
    if not target_path.is_absolute():
        target_path = REPO_ROOT / target_path

    try:
        target_rel = target_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        print(f"Target must be inside repo: {target_path}")
        return

    template = read_text(S0_TASK_PROMPT)
    prompt_text = f"""{template}

<TARGET_PATH>
{target_rel}
</TARGET_PATH>

<WORKFLOW_CONTEXT>
{load_workflow_context()}
</WORKFLOW_CONTEXT>

<POS_CONTEXT>
{load_pos_context()}
</POS_CONTEXT>

<FILE_CONTEXT>
{load_single_file_context(target_path)}
</FILE_CONTEXT>
"""
    LAST_PROMPT_PATH.write_text(prompt_text, encoding="utf-8")

    output = call_llm(
        DEFAULT_MODEL,
        system_prompt="You are a strict minimal-scope repo task drafting agent.",
        user_text=prompt_text,
        file_path=str(target_path),
        max_retries=2,
        timeout=120,
    )

    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(output, encoding="utf-8")
    print(output)
    print(f"\nSaved task: {task_path}")

def main() -> None:
    task_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("task.md")
    if not task_path.is_absolute():
        task_path = Path.cwd() / task_path

    draft_target = flag_value("--draft-task")
    if "--draft-task" in sys.argv:
        if not draft_target:
            print("Usage: python agent/agent.py agent/task.md --draft-task path/to/file.py")
            return
        draft_task(task_path, draft_target)
        return

    # Inspection / state-management modes: no LLM call.
    if "--status" in sys.argv:
        print_status(task_path)
        return

    if "--show-last" in sys.argv:
        show_last_plan()
        return

    if "--accept-last" in sys.argv:
        accept_last_plan()
        return

    if "--clear-trace" in sys.argv:
        clear_trace()
        return

    if "--show-final" in sys.argv:
        show_final_plan()
        return

    if "--check-ready" in sys.argv:
        check_ready(task_path)
        return

    if "--show-commands" in sys.argv:
        show_commands()
        return

    if "--check-patch" in sys.argv:
        check_patch(task_path)
        return

    if "--apply-patch" in sys.argv:
        apply_patch(task_path)
        return

    if "--run-verify" in sys.argv:
        run_verify()
        return

    task_text = read_text(task_path)

    # LLM refinement modes: read previous artifacts and produce next artifact.
    if "--review-last" in sys.argv:
        plan_text = read_text(LAST_PLAN_PATH)
        review = call_llm(
            DEFAULT_MODEL,
            system_prompt="You are a strict minimal-change repo plan reviewer.",
            user_text=build_review_prompt(task_text, plan_text),
            file_path=str(LAST_PLAN_PATH),
            max_retries=2,
            timeout=120,
        )
        LAST_REVIEW_PATH.write_text(review, encoding="utf-8")
        print(review)
        print(f"\nSaved review: {LAST_REVIEW_PATH}")
        return

    if "--revise-last" in sys.argv:
        plan_text = read_text(LAST_PLAN_PATH)
        review_text = read_text(LAST_REVIEW_PATH)
        revised = call_llm(
            DEFAULT_MODEL,
            system_prompt="You are a strict minimal-change repo plan revision agent.",
            user_text=build_revise_prompt(task_text, plan_text, review_text),
            file_path=str(LAST_REVIEW_PATH),
            max_retries=2,
            timeout=120,
        )
        LAST_REVISED_PLAN_PATH.write_text(revised, encoding="utf-8")
        print(revised)
        print(f"\nSaved revised plan: {LAST_REVISED_PLAN_PATH}")
        return

    if "--make-patch" in sys.argv:
        if not FINAL_PLAN_PATH.exists():
            print("No final plan found.")
            return

        task_text = read_text(task_path)
        allowed_files = parse_allowed_files(task_text)
        file_context = load_allowed_file_context(allowed_files)
        final_plan_text = read_text(FINAL_PLAN_PATH)

        patch = call_llm(
            DEFAULT_MODEL,
            system_prompt="You are a strict minimal-change SEARCH/REPLACE patch generator.",
            user_text=build_patch_prompt(task_text=task_text, final_plan_text=final_plan_text, allowed_files=allowed_files, file_context=file_context),
            file_path=str(FINAL_PLAN_PATH),
            max_retries=2,
            timeout=120,
        )

        LAST_PATCH_PATH.write_text(patch, encoding="utf-8")
        print(f"Saved patch: {LAST_PATCH_PATH}")
        print("Next: python agent/agent.py agent/task.md --check-patch")
        return

    # Default mode: build context and generate the first plan.
    allowed_files = parse_allowed_files(task_text)
    pos_context = load_pos_context()
    file_context = load_allowed_file_context(allowed_files)

    print("=== Repo Planning Agent ===")
    print(f"task: {task_path}")
    print(f"prompt: {S1_PLAN_PROMPT}")
    print(f"model: {DEFAULT_MODEL}")
    print("allowed files:")
    for file_path in allowed_files or ["<none>"]:
        print(f"  - {file_path}")
    print()

    prompt_text = build_plan_prompt(task_text, pos_context, file_context)
    LAST_PROMPT_PATH.write_text(prompt_text, encoding="utf-8")

    if "--dry-context" in sys.argv:
        print(f"Saved prompt: {LAST_PROMPT_PATH}")
        return

    output = call_llm(
        DEFAULT_MODEL,
        system_prompt="You are a strict minimal-change repo planning agent.",
        user_text=prompt_text,
        file_path=str(task_path),
        max_retries=2,
        timeout=120,
    )

    LAST_PLAN_PATH.write_text(output, encoding="utf-8")
    print(output)
    print(f"\nSaved plan: {LAST_PLAN_PATH}")


if __name__ == "__main__":
    main()
