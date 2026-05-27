'''
python3 agent/agent.py --run-iterate-task w/p_wiki.py # steps 0-3 with auto-iteration until APPROVE or unknown verdict
python3 agent/agent.py --draft-task w/p_wiki.py      # step 0
python3 agent/agent.py --run-task  # step 1
python3 agent/agent.py --review-last  # step 2
python3 agent/agent.py --status
python3 agent/agent.py --accept-last  # step 4
python3 agent/agent.py --show-final
python3 agent/agent.py --check-ready
python3 agent/agent.py --show-commands
python3 agent/agent.py --make-patch  # step 5, includes check_patch internally
python3 agent/agent.py --run-verify  
python3 agent/agent.py --apply-patch  # step 6, includes run verify internally
python3 agent/agent.py --clear-trace

Workflow: plan -> review -> revise -> accept -> make/check patch -> apply patch/run verify

Invariants:
1. LLM stages produce artifacts, not direct repo mutations.
2. Human acceptance is required before patch generation.
3. Patch generation must be constrained by allowed files.
4. Patch application must pass exact SEARCH/REPLACE validation.
5. Verification commands run only from the accepted final plan.
'''
from __future__ import annotations


import tempfile
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helper.helper_llm import call_llm  # noqa: E402

DEFAULT_MODEL = "gpt-5.4" # codex, gpt-5.4-mini

CODEX_MODEL = "gpt-5.5"
CODEX_REASONING_EFFORT = "low" # "mid", "high", "xhigh"
ITERATION_LIMIT = 5

POS_ACTIVE_FILES = (
    "AGENTS.md", # agent 怎樣使用 POS，不要污染系統
    "context.md", # 當前方向、當前重點、近期約束
    "assets.md", # 已穩定、可調用的判斷規則
)

POS_ARCHIVE_FILES = ( # not actively used by agent, but kept for record and future reference
    "decisions.md",
    "proposals.md",
)

S0_TASK_PROMPT = REPO_ROOT / "agent" / "prompt_s0_agent_task.txt"
S1_PLAN_PROMPT = REPO_ROOT / "agent" / "prompt_s1_agent_plan.txt"
S2_REVIEW_PROMPT = REPO_ROOT / "agent" / "prompt_s2_agent_review.txt"
S3_REVISE_PROMPT = REPO_ROOT / "agent" / "prompt_s3_agent_revise.txt"
S5_PATCH_PROMPT = REPO_ROOT / "agent" / "prompt_s5_agent_patch.txt"

AGENT_DATA_DIR = REPO_ROOT / "data" / "agent"
S0_TASK_PATH = AGENT_DATA_DIR / "s0_task.md"
S0_PROMPT_PATH = AGENT_DATA_DIR / "s0_prompt.txt"
S1_PROMPT_PATH = AGENT_DATA_DIR / "s1_prompt.txt"
S1_PLAN_PATH = AGENT_DATA_DIR / "s1_plan.md"
S2_REVIEW_PATH = AGENT_DATA_DIR / "s2_review.md"
S3_REVISED_PLAN_PATH = AGENT_DATA_DIR / "s3_revised_plan.md"
S4_FINAL_PLAN_PATH = AGENT_DATA_DIR / "s4_final_plan.md"
S5_PATCH_PATH = AGENT_DATA_DIR / "s5_patch.txt"


def plan_path(attempt: int) -> Path:
    return AGENT_DATA_DIR / f"s1_plan_{attempt}.md"


def review_path(attempt: int) -> Path:
    return AGENT_DATA_DIR / f"s2_review_{attempt}.md"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_optional(path: Path) -> str:
    return read_text(path) if path.exists() else ""


def ensure_agent_data_dir() -> None:
    AGENT_DATA_DIR.mkdir(parents=True, exist_ok=True)

def call_codex_cli(system_prompt: str, user_text: str, context_path: Path, *, timeout: int = 900) -> str:
# Safety boundary:
# This backend is used as a text-generation backend for planning/review/patch artifacts.
# If Codex CLI is run with write-capable flags, it may violate the intended invariant
# that repo mutation only happens through apply_patch().
    ensure_agent_data_dir()
    prompt_text = "\n\n".join(part for part in (system_prompt.strip(), user_text.strip()) if part)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", dir=AGENT_DATA_DIR, delete=False) as temp:
        output_path = Path(temp.name)

    try:
        cmd = [
            "codex", "exec",
            "--cd", str(REPO_ROOT),
            # "--yolo", Codex backend is trusted high-risk backend
            "--color", "never",
            "--model", CODEX_MODEL,
            "-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
            "--output-last-message", str(output_path),
            "-",
        ]

        result = subprocess.run(cmd, input=prompt_text, text=True, cwd=REPO_ROOT, capture_output=True, timeout=timeout)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Codex CLI failed for {repo_rel(context_path)}:\n{detail}")

        output = read_text(output_path).strip()
        return output or result.stdout.strip()
    finally:
        output_path.unlink(missing_ok=True)

def call_agent_llm(system_prompt: str, user_text: str, file_path: Path) -> str:
    if DEFAULT_MODEL == "codex":
        return call_codex_cli(system_prompt, user_text, context_path=file_path)
    return call_llm(DEFAULT_MODEL, system_prompt=system_prompt, user_text=user_text, file_path=str(file_path), max_retries=2, timeout=120)

def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def argv_value_after(flag: str) -> str | None:
    if flag not in sys.argv:
        return None
    index = sys.argv.index(flag)
    if index + 1 >= len(sys.argv):
        return None
    value = sys.argv[index + 1]
    return None if value.startswith("--") else value


def parse_allowed_files(task_text: str) -> list[str]:
    match = re.search(r"(?im)^##\s*Allowed files\s*$([\s\S]*?)(?=^##\s|\Z)", task_text)
    if not match:
        return []

    files: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            files.append(line[2:].strip().strip("`"))
    return files


def load_pos_context() -> str:
    parts: list[str] = []
    for name in POS_ACTIVE_FILES:
        path = REPO_ROOT / "pos" / name
        text = read_optional(path).strip()
        if text:
            parts.append(f"# pos/{name}\n\n{text}")
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


def build_plan_prompt(task_text: str, pos_context: str, file_context: str, previous_plan_text: str = "", previous_review_text: str = "") -> str:
    # Step 1: plan.
    template = read_text(S1_PLAN_PROMPT)
    return template.format(task_text=task_text, pos_context=pos_context, file_context=file_context, previous_plan_text=previous_plan_text, previous_review_text=previous_review_text)

def build_review_prompt(task_text: str, plan_text: str, pos_context: str) -> str:
    # Step 2: review.
    template = read_text(S2_REVIEW_PROMPT)
    return template.format(task_text=task_text, plan_text=plan_text, pos_context=pos_context)

def build_revise_prompt(task_text: str, plan_text: str, review_text: str, pos_context: str) -> str:
    # Step 3: revise.
    template = read_text(S3_REVISE_PROMPT)
    return template.format(task_text=task_text, plan_text=plan_text, review_text=review_text, pos_context=pos_context)

def latest_plan_path() -> Path | None:
    attempt = latest_attempt()
    return plan_path(attempt) if attempt is not None else None


def review_verdict(review_text: str) -> str | None:
    match = re.search(r"(?im)^\s*-?\s*\*{0,2}(APPROVE|REVISE)\*{0,2}\s*$", review_text)
    return match.group(1) if match else None

def print_status(task_path: Path) -> None:
    attempt = latest_attempt()
    paths = (
        ("s0_task", task_path),
        ("latest_plan", plan_path(attempt) if attempt is not None else None),
        ("latest_review", review_path(attempt) if attempt is not None else None),
        ("s4_final_plan", S4_FINAL_PLAN_PATH),
        ("s5_patch", S5_PATCH_PATH),
    )

    print("=== Agent Trace Status ===")
    print(f"latest_attempt: {attempt if attempt is not None else '<none>'}")
    for name, path in paths:
        if path is None:
            print(f"{name}: missing")
            continue
        status = "exists" if path.exists() else "missing"
        print(f"{name}: {status} - {path}")

def accept_last_plan() -> None:
    # Accept latest plan attempt as final snapshot.
    source = latest_plan_path()
    if source is None:
        print("No plan found to accept.")
        return

    content = read_text(source)
    ensure_agent_data_dir()
    S4_FINAL_PLAN_PATH.write_text(f"# Final Accepted Plan\n\nSource: `{repo_rel(source)}`\n\n---\n\n{content}", encoding="utf-8")
    print(f"Accepted plan: {S4_FINAL_PLAN_PATH}")


def clear_trace() -> None:
    paths = [
        S0_TASK_PATH,
        S0_PROMPT_PATH,
        S1_PROMPT_PATH,
        S1_PLAN_PATH,
        S2_REVIEW_PATH,
        S3_REVISED_PLAN_PATH,
        S4_FINAL_PLAN_PATH,
        S5_PATCH_PATH,
    ]
    paths.extend(sorted(AGENT_DATA_DIR.glob("s1_plan_*.md")))
    paths.extend(sorted(AGENT_DATA_DIR.glob("s2_review_*.md")))

    seen = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)

        if path.exists():
            path.unlink()
            print(f"removed: {path}")
        else:
            print(f"missing: {path}")

    print("Trace cleared.")


def show_final_plan() -> None:
    if not S4_FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return

    print("=== Final Accepted Plan ===")
    print(f"source: {S4_FINAL_PLAN_PATH}\n")
    print(read_text(S4_FINAL_PLAN_PATH))


def check_ready(task_path: Path) -> None:
    checks = []

    checks.append(("task exists", task_path.exists()))
    checks.append(("s4_final_plan exists", S4_FINAL_PLAN_PATH.exists()))

    task_text = read_text(task_path) if task_path.exists() else ""
    allowed_files = parse_allowed_files(task_text) if task_text else []

    checks.append(("allowed files declared", bool(allowed_files)))

    for rel in allowed_files:
        checks.append((f"allowed file exists: {rel}", (REPO_ROOT / rel).exists()))

    final_text = read_text(S4_FINAL_PLAN_PATH) if S4_FINAL_PLAN_PATH.exists() else ""
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
    text = read_text(S4_FINAL_PLAN_PATH)
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
    if not S4_FINAL_PLAN_PATH.exists():
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
    # Run verify commands from the accepted plan.
    if not S4_FINAL_PLAN_PATH.exists():
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

def build_patch_prompt(task_text: str, final_plan_text: str, allowed_files: list[str], file_context: str, pos_context: str) -> str:
    # Step 5: make patch
    template = read_text(S5_PATCH_PROMPT)
    return template.format(task_text=task_text, final_plan_text=final_plan_text, allowed_files_text="\n".join(f"- {p}" for p in allowed_files), file_context=file_context, pos_context=pos_context)


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


def check_patch(task_path: Path) -> bool:
    # Check patch.
    if not S5_PATCH_PATH.exists():
        print("No patch found.")
        return False

    patch_text = read_text(S5_PATCH_PATH)
    if patch_text.strip() == "PATCH_NOT_SAFE":
        print("PATCH_NOT_SAFE")
        return False

    blocks, errors = parse_patch_blocks(patch_text)
    errors.extend(validate_patch_blocks(task_path, blocks))

    if errors:
        print("PATCH_INVALID")
        for error in errors:
            print(error)
        return False

    print("PATCH_OK")
    return True


def apply_patch(task_path: Path) -> None:
    # Step 6: apply patch and run verify.
    if not S5_PATCH_PATH.exists():
        print("No patch found.")
        return

    patch_text = read_text(S5_PATCH_PATH)
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
    run_verify()


def draft_task(task_path: Path, target_arg: str | None = None) -> None:
    # Step 0: draft s0_task.md from one target source file.
    target_arg = target_arg or argv_value_after("--draft-task")
    if not target_arg:
        print("Usage: python agent/agent.py --draft-task path/to/file.py")
        return

    target_path = Path(target_arg)
    if not target_path.is_absolute():
        target_path = REPO_ROOT / target_path

    try:
        target_rel = target_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        print(f"Target must be inside repo: {target_path}")
        return

    template = read_text(S0_TASK_PROMPT)
    pos_context = load_pos_context()
    prompt_text = f"""
<POS_CONTEXT>
{pos_context}
</POS_CONTEXT>

<FILE_CONTEXT>
{load_single_file_context(target_path)}
</FILE_CONTEXT>

<TARGET_PATH>
{target_rel}
</TARGET_PATH>

{template}
"""
    ensure_agent_data_dir()
    S0_PROMPT_PATH.write_text(prompt_text, encoding="utf-8")

    output = call_agent_llm(
        system_prompt="You are a strict minimal-scope repo task drafting agent.",
        user_text=prompt_text,
        file_path=target_path,
    )

    task_path.write_text(output, encoding="utf-8")
    print(output)
    print(f"\nSaved task: {task_path}")


def run_task(task_path: Path, attempt: int | None = None) -> str:
    # Step 1: generate one planning attempt.
    if attempt is None:
        latest = latest_attempt()
        if latest is None:
            attempt = 1
        else:
            current_review_path = review_path(latest)
            if not current_review_path.exists():
                print(f"Latest plan has no review yet: {plan_path(latest)}")
                print("Next: python3 agent/agent.py --review-last")
                return ""
            attempt = latest + 1

    task_text = read_text(task_path)
    allowed_files = parse_allowed_files(task_text)
    pos_context = load_pos_context()
    file_context = load_allowed_file_context(allowed_files)
    previous_plan_text = ""
    previous_review_text = ""

    if attempt > 1:
        previous_plan_text = read_text(plan_path(attempt - 1))
        previous_review_text = read_text(review_path(attempt - 1))

    print("=== Repo Planning Agent ===")
    print(f"task: {task_path}")
    print(f"attempt: {attempt}")
    print(f"prompt: {S1_PLAN_PROMPT}")
    print(f"model: {DEFAULT_MODEL}")
    print("allowed files:")
    for file_path in allowed_files or ["<none>"]:
        print(f"  - {file_path}")
    print()

    prompt_text = build_plan_prompt(task_text, pos_context, file_context, previous_plan_text=previous_plan_text, previous_review_text=previous_review_text)
    ensure_agent_data_dir()
    S1_PROMPT_PATH.write_text(prompt_text, encoding="utf-8")

    output = call_agent_llm(
        system_prompt="You are a strict minimal-change repo iteration planning agent.",
        user_text=prompt_text,
        file_path=task_path,
    )

    path = plan_path(attempt)
    path.write_text(output, encoding="utf-8")
    print(output)
    print(f"\nSaved plan: {path}")
    return output

def latest_attempt() -> int | None:
    attempts = []
    for path in AGENT_DATA_DIR.glob("s1_plan_*.md"):
        match = re.fullmatch(r"s1_plan_(\d+)\.md", path.name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts) if attempts else None

def review_last_plan(task_path: Path) -> str:
    # Step 2: review latest plan attempt.
    attempt = latest_attempt()
    if attempt is None:
        print("No plan found to review.")
        return ""

    path = plan_path(attempt)
    task_text = read_text(task_path)
    plan_text = read_text(path)

    review = call_agent_llm(
        system_prompt="You are a strict minimal-change repo plan reviewer.",
        user_text=build_review_prompt(task_text, plan_text, pos_context=load_pos_context()),
        file_path=path,
    )

    output_path = review_path(attempt)
    ensure_agent_data_dir()
    output_path.write_text(review, encoding="utf-8")
    print(review)
    print(f"\nSaved review: {output_path}")
    return review

def run_iterate_task(task_path: Path) -> None:
    target_arg = argv_value_after("--run-iterate-task")

    for path in AGENT_DATA_DIR.glob("s1_plan_*.md"):
        path.unlink()
    for path in AGENT_DATA_DIR.glob("s2_review_*.md"):
        path.unlink()

    if target_arg:
        draft_task(task_path, target_arg=target_arg)

    run_task(task_path, 1)

    for attempt in range(1, ITERATION_LIMIT + 1):
        review = review_last_plan(task_path)
        verdict = review_verdict(review)

        if verdict == "APPROVE":
            print("ITERATE_APPROVED")
            accept_last_plan()
            return

        if verdict != "REVISE":
            print("ITERATE_STOPPED: unknown review verdict")
            return

        if attempt == ITERATION_LIMIT:
            print("ITERATE_STOPPED: max iterations reached")
            return

        run_task(task_path, attempt + 1)

def make_patch(task_path: Path) -> None:
    # Step 5: make/check patch.
    if not S4_FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return

    task_text = read_text(task_path)
    allowed_files = parse_allowed_files(task_text)
    file_context = load_allowed_file_context(allowed_files)
    final_plan_text = read_text(S4_FINAL_PLAN_PATH)

    patch = call_agent_llm(
        system_prompt="You are a strict minimal-change SEARCH/REPLACE patch generator.",
        user_text=build_patch_prompt(task_text=task_text, final_plan_text=final_plan_text, allowed_files=allowed_files, file_context=file_context, pos_context=load_pos_context()),
        file_path=S4_FINAL_PLAN_PATH,
    )

    ensure_agent_data_dir()
    S5_PATCH_PATH.write_text(patch, encoding="utf-8")
    print(f"Saved patch: {S5_PATCH_PATH}")
    if check_patch(task_path):
        print("Next: python agent/agent.py --apply-patch")


def main() -> None:
    if "--draft-task" in sys.argv: draft_task(S0_TASK_PATH); return
    if "--run-iterate-task" in sys.argv: run_iterate_task(S0_TASK_PATH); return
    if "--run-task" in sys.argv: run_task(S0_TASK_PATH); return

    # Continue LLM stages from the current task and saved artifacts.
    if "--review-last" in sys.argv: review_last_plan(S0_TASK_PATH); return
    if "--make-patch" in sys.argv: make_patch(S0_TASK_PATH); return

    # Inspection / state-management modes: no LLM call.
    if "--status" in sys.argv: print_status(S0_TASK_PATH); return
    if "--accept-last" in sys.argv: accept_last_plan(); return
    if "--clear-trace" in sys.argv: clear_trace(); return
    if "--show-final" in sys.argv: show_final_plan(); return
    if "--check-ready" in sys.argv: check_ready(S0_TASK_PATH); return
    if "--show-commands" in sys.argv: show_commands(); return
    if "--apply-patch" in sys.argv: apply_patch(S0_TASK_PATH); return
    if "--run-verify" in sys.argv: run_verify(); return


if __name__ == "__main__":
    main()
