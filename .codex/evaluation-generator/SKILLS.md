---

name: evaluation-generator
description: Create, update, or review an isolated `evaluation.py`-style unittest evaluator for Python projects.
----------------------------------------------------------------------------------------------------------------

# Evaluation Generator Skill

Create a standalone, deterministic, `unittest`-based evaluator that verifies meaningful behavior of a Python project without requiring credentials, network access, production data, local services, or manual setup.

The evaluator is for defect detection, not cosmetic coverage. Do not weaken tests to make broken code pass. If the target project has a real defect, expose it with a focused failing test.

## Required Output

Generate or update one of these files, depending on project layout:

* `evaluation.py` for simple/root-level projects
* `tests/evaluation.py` for package-style projects
* `evaluation/evaluation.py` if that convention already exists

Use the existing convention when present. Otherwise prefer the simplest location that can run directly.

The evaluator must use only Python standard-library testing tools unless explicitly requested otherwise.

Preferred imports:

* `unittest`
* `unittest.mock`
* `tempfile`
* `pathlib`
* `os`
* `sys`
* `importlib`
* `time`
* `warnings`

Do not introduce `pytest` unless the user explicitly asks for it.

## Direct Execution Contract

The evaluator must be directly executable:

```python
if __name__ == "__main__":
    unittest.main(testRunner=EmojiTextTestRunner, verbosity=2)
```

Use safe project-root setup.

For root-level `evaluation.py`:

```python
ROOT = Path(__file__).resolve().parent
```

For `tests/evaluation.py` or `evaluation/evaluation.py`:

```python
ROOT = Path(__file__).resolve().parents[1]
```

Then:

```python
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

Do not hardcode absolute paths. Do not insert unrelated directories.

## Required Emoji Runner

Implement a custom `unittest.TextTestResult` and `unittest.TextTestRunner`.

The runner must print:

```text
✅ PASS
❌ FAIL
```

Do not print a separate `❌ ERROR` marker. Unexpected errors must be displayed and counted as failures.

Final summary must use this exact shape:

```text
Ran N tests in X.XXXs

Passed: N ✅
Failed: N ❌
```

The result class should:

* override `startTest()` to optionally print a group header
* override `addSuccess()` to print `✅ PASS`
* override `addFailure()` to print `❌ FAIL`
* override `addError()` to print `❌ FAIL`
* optionally group by `target_file`, `target_module`, or `behavior_area`

The runner should calculate:

```python
failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
passed = result.testsRun - failed - len(result.skipped) - len(result.expectedFailures)
```

Only print skipped or expected-failure counts if the evaluator intentionally uses them.

## Inspect Before Testing

Before writing or updating tests, inspect the project structure.

Identify:

* whether the project is a single file, loose scripts, or a package
* import roots and package names
* public functions, classes, and CLI entry points
* important internal helpers that encode contracts
* side-effect boundaries
* configuration loading
* validation and guard logic
* parsing, routing, state transitions, and persistence
* existing tests or evaluator conventions
* likely failure modes

Do not assume import paths. Derive them from the file layout.

## Test Selection Priority

Do not test every file equally. Prioritize behavior with real failure risk.

Highest-priority targets:

1. Public APIs and CLI behavior
2. Parsing, validation, normalization, and routing
3. Security or safety guards
4. Configuration loading and defaults
5. State transitions and persistence decisions
6. External-service boundaries
7. Failure and cleanup paths
8. Edge cases that are easy to break

It is acceptable to test internal helpers when they define important project contracts, such as recipient validation, config parsing, route selection, data normalization, path guards, or destructive-action prevention.

Avoid tests that only assert implementation trivia.

## What to Cover

Include focused tests for:

* valid input and expected output
* invalid input and expected rejection
* missing or malformed config
* empty, whitespace, duplicate, absent, or boundary values
* optional behavior enabled/disabled
* downstream exceptions
* cleanup/finalization on failure
* skipped external actions when unsafe or disabled
* safe arguments passed to mocked side-effect boundaries

For side-effect-heavy code, assert the boundary behavior:

* network is not called when disabled
* configured timeout is used
* clients are closed or disconnected
* unsafe recipients, paths, or arguments are refused
* delete/send/overwrite operations do not target unintended objects

## Mocking and Isolation

Mock all real external services and side effects.

Mock:

* HTTP/API/network clients
* SMTP, IMAP, email send/fetch operations
* databases
* subprocesses
* LLM calls
* RAG/search services
* cloud services
* current time
* randomness
* sleeps and timers
* environment variables
* production filesystem paths

Use:

* `unittest.mock.patch`
* `MagicMock`
* small fake objects
* `tempfile.TemporaryDirectory()`
* `tempfile.NamedTemporaryFile()`
* `patch.dict(os.environ, {...}, clear=True)`

Each test must be independent.

Do not rely on test order. Do not leave modified environment variables, files, caches, loggers, monkeypatches, or global state behind. Use `setUp()`, `tearDown()`, `addCleanup()`, or context-manager patches.

Patch dependencies where they are looked up by the target module.

Examples:

```python
# app.sender imports smtplib
patch("app.sender.smtplib.SMTP")

# app.pipeline does: from app.llm import call_llm
patch("app.pipeline.call_llm")
```

## Assertion Style

Prefer behavior and contract assertions.

Good assertions:

* returned object has expected fields
* invalid input raises the expected exception
* failure result contains expected error state
* external boundary was called with safe expected arguments
* external boundary was not called when disabled or unsafe
* cleanup happens even after exception

Avoid brittle assertions:

* exact private call sequence unless it is the contract
* incidental log wording unless logs are the output contract
* temporary variable behavior
* line-by-line implementation details
* asserting many internal calls when one behavior assertion is enough

## Organization and Naming

Group tests by target file, module, class, or behavior area.

For multi-file projects, use classes such as:

```python
class ConfigParserTests(unittest.TestCase):
    target_file = "config.py"
```

```python
class RoutingTests(unittest.TestCase):
    behavior_area = "routing"
```

For single-file scripts, group by behavior area:

* parsing
* validation
* transformation
* persistence
* CLI behavior
* failure handling

Use straightforward test names:

```python
test_parses_valid_config
test_rejects_missing_required_field
test_handles_empty_input
test_uses_timeout_without_network_call
test_skips_send_when_recipient_is_invalid
test_disconnects_client_when_fetch_fails
test_preserves_existing_subject_prefix
```

Avoid vague names:

```python
test_1
test_stuff
test_function
test_case_a
```

## Fixtures and Helper Factories

Use small helper factories when they make tests clearer.

Examples:

```python
_email(**overrides)
_config(**overrides)
_raw_record(**headers)
_response(**overrides)
_tmp_project(files)
```

Factories should provide realistic defaults, accept keyword overrides, and return project-native objects when practical.

Avoid large opaque fixtures that hide the behavior being tested.

## Single-File Script Rules

For a single Python file:

* import it as a module when possible
* test public functions first
* test CLI behavior by patching `sys.argv`, `input`, `print`, subprocesses, filesystem, and network boundaries
* avoid triggering dangerous top-level behavior during import
* if needed and within task scope, recommend or add a safe `main()` boundary before testing

If import has unavoidable side effects, patch them before import or use `importlib` carefully.

## Multi-File Package Rules

For a package:

* test modules by responsibility
* group tests by file/module/behavior
* mock across module boundaries at the target module lookup site
* test public integration boundaries without invoking real external services
* prioritize behavior-heavy modules over passive data or constant files

Do not force equal test coverage across all files.

## Updating Existing Evaluators

When updating an evaluator:

* preserve useful tests
* preserve existing runner/output style unless it violates this skill
* keep naming and grouping consistent
* add focused tests for newly discovered behavior or regressions
* remove redundant or brittle tests only when replacing them with stronger behavior tests
* do not rewrite the whole evaluator unnecessarily

If an existing evaluator prints `❌ ERROR`, change it so errors print `❌ FAIL` and count as failures.

## Defect Handling

If target code appears broken, write the focused failing test that exposes the defect.

Do not silently adapt the evaluator to broken behavior.

Do not mock away the defect unless it is outside evaluator scope.

Prefer one precise failing test over many broad failing tests.

## Minimal Evaluator Structure

A generated evaluator should normally contain:

1. shebang
2. module docstring describing evaluator scope
3. `from __future__ import annotations`
4. imports
5. safe `ROOT` and `sys.path` setup
6. target project imports
7. `EmojiTextTestResult`
8. `EmojiTextTestRunner`
9. helper factories
10. grouped `unittest.TestCase` classes
11. direct executable entry point

## Quality Bar

A good evaluator should answer:

* What behavior does this project promise?
* What inputs are valid or invalid?
* What side effects are allowed?
* What must never happen?
* What happens when dependencies fail?
* What edge cases are likely to regress?
* Which boundaries separate this code from the outside world?

Prefer compact, high-signal tests over maximum test count.
