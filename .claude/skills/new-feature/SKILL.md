---
name: new-feature
description: Scaffold a new pipeline feature — creates a spec doc, test fixture directory, and a failing test, then hands off to implementation. Use when starting any non-trivial addition to src/.
argument-hint: "<feature-name>"
allowed-tools: Read, Write, Edit, Glob, Bash(ls *), Bash(mkdir -p *)
---

# New feature: $ARGUMENTS

Scaffold the feature before writing any implementation code.

## Steps

1. **Spec.** Create `specs/feature-$ARGUMENTS.md` with sections: Problem, Inputs, Outputs, Edge cases, Test cases, Non-goals. Do not skip any section — write "N/A" if truly not applicable, with a one-line reason.
2. **Fixtures.** Create `tests/fixtures/$ARGUMENTS/` and add at least one minimal input + expected-output pair covering the happy path.
3. **Failing test.** Add `tests/test_$ARGUMENTS.py` with one test that exercises the happy-path fixture. Assert the expected output. It should fail with `ImportError` or `NotImplementedError` — that's fine, that's the RED in RED-GREEN-REFACTOR.
4. **Stop.** Do NOT implement. Report back with:
   - the spec path,
   - the fixture paths,
   - the test command to run,
   - and a one-paragraph summary of the intended design.

The user will review the spec before you start implementation.

## Rules

- Never skip the spec. A feature without a spec is a bug waiting to happen.
- Tests go in `tests/` mirroring `src/` structure.
- Fixtures are the source of truth for expected behavior — they're reviewed alongside the spec.
