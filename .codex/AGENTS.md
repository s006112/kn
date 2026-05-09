# Response Contract

Default behavior:

* NEVER output complete full files unless explicitly requested.
* Prefer minimal patch/diff style responses.
* Only show changed functions or changed blocks.
* Preserve all untouched code exactly.
* Avoid rewrite-style responses.

Code modification policy:

* Minimize code churn.
* Preserve architecture and existing semantics.
* Refactor only when explicitly requested.
* Avoid unnecessary abstraction/helper layers.
* Prefer local changes over global rewrites.

Output policy:

* Show:

  * changed functions
  * exact insertion points
  * unified diff
  * minimal replacement blocks
* Do NOT dump unchanged code.

When full file output is allowed:

* User explicitly requests:

  * "complete direct drop in code"
  * "full file"
  * "rewrite entire file"
* Or patch ambiguity makes partial output unsafe.

Token efficiency:

* Optimize for minimum output tokens.
* Avoid repeating unchanged imports/constants/classes.
* Avoid explanatory essays unless requested.

Engineering preference:

* Dense, clean, boundary-clear code.
* Eliminate redundant helpers and duplicate logic.
* Preserve functionality exactly.
* Favor readability through simplification, not abstraction.
