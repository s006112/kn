# Alignment Patch Format

## Rules

- only include changed sections
- no full file dump
- preserve original structure
- no extra explanation inside patch

---

## Format
### file: <filename>

<language>
<modified section only>

---

## Example

### file: flow_engine.md

### 3. Main Loop

1. sleep
2. get open orders
3. call strategy decision layer

---

## Anti-patterns

* ❌ full file rewrite
* ❌ mixed explanation inside code
* ❌ introducing new semantics
