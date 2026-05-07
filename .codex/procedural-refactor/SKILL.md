---
name: procedural-refactor
description: Reduce object-oriented style and unnecessary abstraction in codebases of any size, especially Python scripts, automation tools, CLIs, web apps, data pipelines, trading bots, LLM/RAG workflows, and file-processing systems. Use when the user asks for C-style, procedural, explicit, low-abstraction, easier-to-read code; when they mention a C background; when classes, inheritance, managers, processors, services, engines, handlers, factories, callbacks, or hidden object state make code hard to follow; or when reviewing/refactoring code toward plain data structures plus ordinary functions.
---

# Procedural Refactor

## Overview

Prefer explicit data, ordinary functions, and visible control flow. Treat readability for a C-background maintainer as the main design constraint.

Use this skill to review, plan, or implement refactors that remove avoidable OO style and speculative abstractions. Do not introduce a new layer, object, framework, registry, or generic mechanism unless it removes real complexity already present in the code.

## Core Rules

- Prefer plain dictionaries, tuples, lists, and dataclasses as data records, similar to C structs.
- Prefer `do_work(config, state, input)` over `object.do_work(input)` when the object only stores config or state.
- Prefer explicit parameters and return values over methods that read and mutate hidden fields.
- Prefer simple modules grouped by job: parse, validate, compute, call_api, write_output, archive, report.
- Keep external-resource wrappers only when they pay for themselves: database connection, network client, loaded model, FAISS index, GUI app object, or required framework callback.
- Keep library-required classes thin. They should translate framework events into calls to ordinary functions.
- Avoid inheritance for behavior variants. Use explicit parameters, small helper functions, or simple `if mode == ...` branches when there are only a few modes.
- Avoid “manager”, “processor”, “service”, “engine”, “handler”, “factory”, and “registry” names unless the code truly manages long-lived resources or many implementations.
- Avoid generic frameworks for local problems. Two similar cases do not justify a plugin system.
- Preserve behavior first. Make small passes and keep tests, smoke checks, or manual run commands close to each change.

## Review Workflow

1. Map the current behavior as data flow:
   `inputs -> validation -> transformation -> side effects -> outputs`.
2. Identify all state:
   config, globals, object fields, queues, locks, caches, temp files, databases, remote services, and persisted files.
3. Classify each class or abstraction:
   - `keep`: required by a framework or owns a real external resource.
   - `struct`: data-only container; keep as dataclass/dict or simplify.
   - `remove`: behavior wrapper; convert methods into ordinary functions.
4. Identify repeated code separately from repeated shape. Do not abstract until there is meaningful duplication, not just visual similarity.
5. Propose the smallest procedural target before editing.
6. Refactor one behavior path at a time. Avoid whole-project rewrites unless the user explicitly asks.

## Procedural Shapes

Use whichever shape fits the code. Do not force every project into the same layout.

For a small script:

```text
constants
parse_args()
load_config()
main()
small helper functions
```

For a CLI or automation tool:

```text
config.py       # constants, paths, model names, feature flags
io_ops.py       # file/network read/write helpers
logic.py        # pure or mostly pure transformations
main.py         # argument parsing and top-level sequence
```

For a long-running worker or pipeline:

```text
config.py       # paths, intervals, model names, feature flags
state.py        # queues, locks, shutdown flag, counters
scan.py         # discover work
queue_ops.py    # enqueue, dedupe, task lifecycle
process_*.py    # process one task type
archive.py      # success/failure movement
main.py         # startup, threads, signal handling
```

For a Flask/Gradio app:

```text
app.py          # routes/UI wiring only
forms.py        # parse and validate request fields
logic.py        # compute/generate result
storage.py      # uploads, database writes, filesystem writes
```

For trading or automation with real-world side effects:

```text
config.py       # explicit risk and API settings
read_state.py   # fetch balances/orders/market data
decision.py     # pure decision function
execute.py      # place/cancel/send side effects
main.py         # loop, timing, logging
```

Preferred function shape:

```python
def process_item(config, state, item):
    data = read_input(config, item)
    result = transform(config, data)
    output = write_output(config, item, result)
    return output
```

## Refactoring Patterns

Convert behavior-only classes:

```python
# Before
processor = ReportProcessor(config)
processor.process(path)

# After
process_report(config, path)
```

Convert inheritance variants:

```python
# Before
class PremiumExtractHandler(BaseExtractHandler): ...
class StandardExtractHandler(BaseExtractHandler): ...

# After
process_extract(config, path, models=models, enable_distill=False)
```

Convert hidden object state:

```python
# Before
runner.config = config
runner.queue = queue
runner.run()

# After
run_worker(config, queue, shutdown_flag)
```

Keep legitimate resource wrappers:

```python
engine = load_rag_engine(config)  # owns loaded index/model state
answer = answer_question(engine, query)
```

The wrapper is acceptable if loading it repeatedly would be expensive or unsafe.

## Decision Rules

- Use a dataclass when several fields travel together and the structure is stable.
- Use a dict when config is loose, external, or still changing.
- Use a class only when it owns a real resource, satisfies a framework interface, or makes lifecycle management simpler.
- Use a plain function when behavior can be expressed from explicit inputs.
- Stop abstracting when the next reader would need to jump files to understand one ordinary operation.

## Output Style

- Explain OO concepts with C analogies: class as struct plus functions, object fields as hidden state, method call as function call with an implicit first argument.
- Be concrete. Name files, functions, state variables, and suggested replacement functions.
- Prefer a small staged plan over a grand architecture.
- Challenge unnecessary abstraction directly and remove it when safe.
- If the user asks only for review or direction, do not rewrite code.
- If the user asks for implementation, make narrow behavior-preserving edits and verify them.
