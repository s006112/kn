'''
python3 agent/agent.py --run-iterate-task helper/helper_ytd.py ali/ali_llm.py ali/ali_router.py agent/agent.py  # agent/agent.py helper/helper_ytd.py w/p_wiki.py
python3 agent/agent.py --draft-task w/p_wiki.py      # step 0
python3 agent/agent.py --run-task  # step 1
python3 agent/agent.py --review-last  # step 2
python3 agent/agent.py --accept-last  # step 3
python3 agent/agent.py --check-ready
python3 agent/agent.py --make-patch  # step 4, includes check_patch internally
python3 agent/agent.py --run-verify  
python3 agent/agent.py --apply-patch  # step 5, includes run verify internally
python3 agent/agent.py --revert-patch  # if step 5 fails, revert and verify again

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

DEFAULT_MODEL = "gpt-5.4-mini" # codex, gpt-5.4-mini

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
S4_PATCH_PROMPT = REPO_ROOT / "agent" / "prompt_s4_agent_patch.txt"

AGENT_DATA_DIR = REPO_ROOT / "agent" / "data"
S0_TASK_PATH = AGENT_DATA_DIR / "s0_task.md"
S0_PROMPT_PATH = AGENT_DATA_DIR / "prompt_s0.txt"
S1_PROMPT_PATH = AGENT_DATA_DIR / "prompt_s1.txt"
S2_PROMPT_PATH = AGENT_DATA_DIR / "prompt_s2.txt"
S4_PROMPT_PATH = AGENT_DATA_DIR / "prompt_s4.txt"
S2_FAULT_LEDGER_PATH = AGENT_DATA_DIR / "s2_fault_ledger.md"
S3_FINAL_PLAN_PATH = AGENT_DATA_DIR / "s3_final_plan.md"
S4_PATCH_PATH = AGENT_DATA_DIR / "s4_patch.txt"


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
    ensure_agent_data_dir()
    prompt_text = "\n\n".join(part for part in (system_prompt.strip(), user_text.strip()) if part)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", dir=AGENT_DATA_DIR, delete=False) as temp:
        output_path = Path(temp.name)
    try:
        result = subprocess.run(
            [
                "codex", "exec",
                "--cd", str(REPO_ROOT),
                "--color", "never",
                "--model", CODEX_MODEL,
                "-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
                "--output-last-message", str(output_path),
                "-",
            ],
            input=prompt_text,
            text=True,
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Codex CLI failed for {repo_rel(context_path)}:\n{detail}")
        return read_text(output_path).strip() or result.stdout.strip()
    finally:
        output_path.unlink(missing_ok=True)

def call_agent_llm(system_prompt: str, user_text: str, file_path: Path) -> str:
    return call_codex_cli(system_prompt, user_text, context_path=file_path) if DEFAULT_MODEL == "codex" else call_llm(DEFAULT_MODEL, system_prompt=system_prompt, user_text=user_text, file_path=str(file_path), max_retries=2, timeout=120)

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
        match_file = re.match(r"^[-*]\s+(.+)$", line)
        if match_file:
            files.append(match_file.group(1).strip().strip("`"))
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

def latest_plan_path() -> Path | None:
    attempt = latest_attempt()
    return plan_path(attempt) if attempt is not None else None


def review_verdict(review_text: str) -> str | None:
    match = re.search(r"(?im)^\s*-?\s*(?:\*\s+)?\*{0,2}(APPROVE|REVISE)\*{0,2}\s*$", review_text)
    return match.group(1) if match else None


def review_section(review_text: str, title: str) -> str:
    heading_pattern = rf"(?im)^\s*(?:[-*]\s*)?\d+\.\s+\*{{0,2}}{re.escape(title)}\*{{0,2}}\s*$"
    match = re.search(heading_pattern, review_text)
    if not match:
        return ""

    next_heading_pattern = (
        r"(?im)^\s*(?:[-*]\s*)?\d+\.\s+\*{0,2}"
        r"(?:Verdict|Reason|Issues|Required revision)"
        r"\*{0,2}\s*$"
    )
    next_match = re.search(next_heading_pattern, review_text[match.end():])
    end = match.end() + next_match.start() if next_match else len(review_text)

    return review_text[match.end():end].strip()

def append_fault_ledger(attempt: int, review_text: str) -> None:
    if review_verdict(review_text) != "REVISE":
        return

    parts = []
    for title in ("Issues", "Required revision"):
        body = review_section(review_text, title)
        if body and body != "None.":
            parts.append(f"## {title}\n\n{body}")

    if not parts:
        parts.append("## Raw review\n\n" + review_text.strip())

    ensure_agent_data_dir()
    entry = f"# Review {attempt}\n\n" + "\n\n".join(parts)
    current = read_optional(S2_FAULT_LEDGER_PATH).strip()
    text = f"{current}\n\n---\n\n{entry}\n" if current else f"{entry}\n"
    S2_FAULT_LEDGER_PATH.write_text(text, encoding="utf-8")


def accept_last_plan() -> None:
    source = latest_plan_path()
    if not source:
        print("No plan found to accept.")
        return

    ensure_agent_data_dir()
    S3_FINAL_PLAN_PATH.write_text(f"# Final Accepted Plan\n\nSource: `{repo_rel(source)}`\n\n---\n\n{read_text(source)}", encoding="utf-8")
    print(f"Accepted plan: {S3_FINAL_PLAN_PATH}")
    print("Next: python agent/agent.py --make-patch")


def check_ready(task_path: Path) -> None:
    task_text = read_text(task_path) if task_path.exists() else ""
    allowed_files = parse_allowed_files(task_text) if task_text else []
    checks = [
        ("task exists", task_path.exists()),
        ("s3_final_plan exists", S3_FINAL_PLAN_PATH.exists()),
        ("allowed files declared", bool(allowed_files)),
        *[(f"allowed file exists: {rel}", (REPO_ROOT / rel).exists()) for rel in allowed_files],
    ]
    final_text = read_text(S3_FINAL_PLAN_PATH) if S3_FINAL_PLAN_PATH.exists() else ""
    checks += [
        ("final plan has evaluation command", "Evaluation command" in final_text or "evaluation" in final_text.lower()),
        ("final plan has stop condition", "Stop condition" in final_text or "停止条件" in final_text),
    ]

    print("=== Agent Ready Check ===")
    ok = True
    for name, passed in checks:
        print(f"{'OK' if passed else 'MISSING'}: {name}")
        ok &= passed
    print()
    print("READY" if ok else "NOT READY")


def get_final_plan_commands() -> list[str]:
    text = read_text(S3_FINAL_PLAN_PATH)
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


def run_verify() -> bool:
    # Run verify commands from the accepted plan.
    if not S3_FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return False

    commands = get_final_plan_commands()

    print("=== Verify Patch Commands ===", flush=True)
    if not commands:
        print("No commands found.", flush=True)
        return True

    for command in commands:
        print(f"$ {command}", flush=True)
        result = subprocess.run(command, cwd=REPO_ROOT, shell=True)
        if result.returncode != 0:
            print(f"VERIFY_FAILED: {command}", flush=True)
            return False

    print("VERIFY_OK", flush=True)
    return True

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


def validate_patch_blocks(
    task_path: Path,
    blocks: list[tuple[str, str, str]],
    *,
    reverse: bool = False,
) -> list[str]:
    allowed_files = set(parse_allowed_files(read_text(task_path) if task_path.exists() else ""))
    errors: list[str] = []
    target_label = "REPLACE" if reverse else "SEARCH"

    for rel, search, replace in blocks:
        path = REPO_ROOT / rel
        target = replace if reverse else search
        if rel not in allowed_files:
            errors.append(f"file not allowed: {rel}")
        elif not path.exists():
            errors.append(f"file not found: {rel}")
        elif not target:
            errors.append(f"empty {target_label} block: {rel}")
        else:
            count = read_text(path).count(target)
            if count != 1:
                errors.append(f"{target_label} match count for {rel}: {count}")

    return errors


def _load_patch_blocks(
    task_path: Path,
    *,
    reverse: bool = False,
) -> list[tuple[str, str, str]] | None:
    if not S4_PATCH_PATH.exists():
        print("No patch found.")
        return None

    patch_text = read_text(S4_PATCH_PATH).strip()
    if patch_text.startswith("PATCH_NOT_SAFE"):
        print(patch_text)
        return None

    blocks, errors = parse_patch_blocks(patch_text)
    errors.extend(validate_patch_blocks(task_path, blocks, reverse=reverse))
    if errors:
        for error in errors:
            print(f"PATCH_INVALID: {error}")
        return None
    return blocks


def check_patch(task_path: Path) -> bool:
    blocks = _load_patch_blocks(task_path)
    if blocks is None:
        return False
    print("PATCH_OK")
    return True


def apply_patch(task_path: Path) -> bool:
    blocks = _load_patch_blocks(task_path)
    if blocks is None:
        print("PATCH_INVALID")
        return False

    for rel, search, replace in blocks:
        path = REPO_ROOT / rel
        path.write_text(read_text(path).replace(search, replace, 1), encoding="utf-8")

    print("PATCH_APPLIED")
    return run_verify()


def revert_patch(task_path: Path) -> bool:
    blocks = _load_patch_blocks(task_path, reverse=True)
    if blocks is None:
        print("PATCH_REVERT_INVALID")
        return False

    for rel, search, replace in blocks:
        path = REPO_ROOT / rel
        path.write_text(read_text(path).replace(replace, search, 1), encoding="utf-8")

    print("PATCH_REVERTED")
    return run_verify()


def draft_task(task_path: Path, target_arg: str | None = None) -> None:
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
    output = _run_prompt_flow(
        S0_PROMPT_PATH,
        S0_TASK_PROMPT,
        "You are a strict minimal-scope repo task drafting agent.",
        target_path,
        pos_context=load_pos_context(),
        file_context=load_single_file_context(target_path),
        target_path=target_rel,
    )
    task_path.write_text(output, encoding="utf-8")
    print(output)
    print(f"\nSaved task: {task_path}")

def _run_prompt_flow(prompt_path: Path, template_path: Path | None, system_prompt: str, file_path: Path, prompt_text: str | None = None, **values: str) -> str:
    if prompt_text is None:
        prompt_text = read_text(template_path).format(**values)
    ensure_agent_data_dir()
    prompt_path.write_text(prompt_text, encoding="utf-8")
    return call_agent_llm(system_prompt=system_prompt, user_text=prompt_text, file_path=file_path)

def _task_context(task_path: Path) -> tuple[str, list[str], str]:
    task_text = read_text(task_path)
    allowed_files = parse_allowed_files(task_text)
    return task_text, allowed_files, load_allowed_file_context(allowed_files)


def run_task(task_path: Path, attempt: int | None = None) -> str:
    if attempt is None:
        latest = latest_attempt()
        if latest is None:
            attempt = 1
        elif not review_path(latest).exists():
            print(f"Latest plan has no review yet: {plan_path(latest)}")
            print("Next: python3 agent/agent.py --review-last")
            return ""
        else:
            attempt = latest + 1
    task_text, allowed_files, file_context = _task_context(task_path)
    print("=== Repo Planning Agent ===")
    print(f"task: {task_path}")
    print(f"attempt: {attempt}")
    print(f"prompt: {S1_PLAN_PROMPT}")
    print(f"model: {DEFAULT_MODEL}")
    print("allowed files:")
    for file_path in allowed_files or ["<none>"]:
        print(f"  - {file_path}")
    print()
    output = _run_prompt_flow(
        S1_PROMPT_PATH,
        S1_PLAN_PROMPT,
        "You are a strict minimal-change repo iteration planning agent.",
        task_path,
        task_text=task_text,
        pos_context=load_pos_context(),
        file_context=file_context,
        fault_ledger_text=read_optional(S2_FAULT_LEDGER_PATH),
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
    attempt = latest_attempt()
    if attempt is None:
        print("No plan found to review.")
        return ""
    path = plan_path(attempt)
    task_text, allowed_files, file_context = _task_context(task_path)
    review = _run_prompt_flow(
        S2_PROMPT_PATH,
        S2_REVIEW_PROMPT,
        "You are a strict minimal-change repo plan reviewer.",
        path,
        task_text=task_text,
        plan_text=read_text(path),
        pos_context=load_pos_context(),
        file_context=file_context,
    )
    output_path = review_path(attempt)
    output_path.write_text(review, encoding="utf-8")
    append_fault_ledger(attempt, review)
    print(review)
    print(f"\nSaved review: {output_path}")
    return review

def make_patch(task_path: Path) -> None:
    if not S3_FINAL_PLAN_PATH.exists():
        print("No final plan found.")
        return
    task_text, allowed_files, file_context = _task_context(task_path)
    prompt_text = read_text(S4_PATCH_PROMPT).format(
        task_text=task_text,
        final_plan_text=read_text(S3_FINAL_PLAN_PATH),
        allowed_files_text="\n".join(f"- {p}" for p in allowed_files),
        file_context=file_context,
    )
    patch = _run_prompt_flow(
        S4_PROMPT_PATH,
        None,
        "You are a strict minimal-change SEARCH/REPLACE patch generator.",
        S3_FINAL_PLAN_PATH,
        prompt_text=prompt_text,
    )
    S4_PATCH_PATH.write_text(patch, encoding="utf-8")
    print(f"Saved patch: {S4_PATCH_PATH}")
    if check_patch(task_path):
        print("Next: python agent/agent.py --apply-patch")
        apply_patch(task_path)


def run_iterate_task(task_path: Path) -> None:
    target_arg = argv_value_after("--run-iterate-task")
    for pattern in ("*.md", "*.txt"):
        for path in AGENT_DATA_DIR.glob(pattern):
            path.unlink()
    S2_FAULT_LEDGER_PATH.unlink(missing_ok=True)
    if target_arg:
        draft_task(task_path, target_arg=target_arg)
    run_task(task_path, 1)
    for attempt in range(1, ITERATION_LIMIT + 1):
        verdict = review_verdict(review_last_plan(task_path))
        if verdict == "APPROVE":
            print("ITERATE_APPROVED")
            accept_last_plan()
            make_patch(task_path)
            return
        if verdict != "REVISE":
            print("ITERATE_STOPPED: unknown review verdict")
            return
        if attempt == ITERATION_LIMIT:
            print("ITERATE_STOPPED: max iterations reached")
            return
        run_task(task_path, attempt + 1)

def main() -> None:
    stage = (
        ("--draft-task", draft_task, (S0_TASK_PATH,)),
        ("--run-iterate-task", run_iterate_task, (S0_TASK_PATH,)),
        ("--run-task", run_task, (S0_TASK_PATH,)),
        ("--review-last", review_last_plan, (S0_TASK_PATH,)),
        ("--make-patch", make_patch, (S0_TASK_PATH,)),
        ("--apply-patch", apply_patch, (S0_TASK_PATH,)),
        ("--revert-patch", revert_patch, (S0_TASK_PATH,)),
        ("--run-verify", run_verify, ()),
        ("--accept-last", accept_last_plan, ()),
        ("--check-ready", check_ready, (S0_TASK_PATH,)),
    )
    for flag, command, args in stage:
        if flag in sys.argv:
            if command(*args) is False:
                sys.exit(1)
            return


if __name__ == "__main__":
    main()
