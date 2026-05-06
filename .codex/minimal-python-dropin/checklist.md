# Minimal Drop-in Checklist

- [ ] I can state the exact required change in one sentence.
- [ ] I listed what must NOT change.
- [ ] I found the smallest edit location.
- [ ] I deleted redundancy before adding code.
- [ ] I did not add abstractions or new interfaces.
- [ ] I did not add a helper for only one caller.
- [ ] Any new helper replaces at least two duplicated blocks.
- [ ] I compared before/after LOC.
- [ ] I compared before/after def/class/helper count.
- [ ] If LOC increased, I explained why it was unavoidable.
- [ ] If this was refactor/cleanup and code did not get smaller, I performed one more compression pass.
- [ ] I ran existing tests or the narrowest verification command.
- [ ] Output is drop-in code unless diff was requested.
- [ ] No clever code. No optional features. No future-proofing.