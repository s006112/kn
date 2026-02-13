# MIGRATION_NOTES

## Unavoidable Differences

1. Runtime platform change
- PHP script execution moved to Flask route execution.
- Impact: URL entrypoint changes from `main.php` to `/` served by `app.py`.
- Future elimination: deploy behind web server rewrite so the public route can mimic legacy path exactly.

2. Session implementation
- PHP server-side `$_SESSION` replaced by Flask signed cookie session.
- Impact: stored parameter values are still persisted across requests, but storage backend differs.
- Future elimination: switch Flask session backend to server-side store if strict infrastructure parity is required.

3. DB driver API
- `mysqli` replaced by `mysql-connector-python`.
- Impact: SQL text and query order remain the same; driver exception messages may differ in wording details.
- Future elimination: none needed unless exact low-level driver error string parity is required.

4. Number formatting runtime
- PHP `number_format` behavior reproduced with Python `Decimal` + `ROUND_HALF_UP` in helper.
- Impact: displayed numeric rounding is aligned to PHP formatting calls used by the page.
- Future elimination: none required for current behavior.

## Included/Dependent PHP Files

- `main.php` has no active `include`/`require` dependencies.
- Comment mentions `general.php`, but no runtime include exists in this file.
