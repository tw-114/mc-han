# mc-han Development Notes

## Current Project State

mc-han currently implements modpack scanning and text extraction, API translation,
translation caching, quality checks, resource/config pack building, installation,
rollback, and a Tkinter GUI. Treat these workflows as existing product behavior,
not future-phase placeholders.

## Safety Boundaries

- Files under `mods/*.jar` are always read-only inputs. Never modify, rewrite, or
  replace a mod JAR.
- Treat every path obtained from a JAR entry, extracted CSV, or install/rollback
  manifest as untrusted input.
- Before reading, writing, installing, restoring, or deleting through an
  input-derived path, resolve it and verify that it remains inside the explicitly
  designated root directory.
- Output paths must remain inside the selected build/output root. Install and
  rollback targets must remain inside the selected modpack root. Backup sources
  must remain inside the selected backup root.
- Preserve resource IDs, JSON keys, placeholders, formatting codes, color codes,
  Patchouli macros, GuideME tags, and code content exactly. Translation logic must
  never translate or structurally damage them.
- Installation and rollback modify user files. Their operations must be
  recoverable after failures: prepare backups and durable recovery metadata before
  destructive writes, and restore the previous state when an operation aborts.

## Secrets And User Data

- Never save, print, log, include in generated output, or commit API keys.
- Never commit real user paths, real credentials, or real test secrets. Tests and
  documentation must use clearly fake values and temporary paths.

## Development Workflow

- Keep every task within its explicitly requested scope. Do not perform unrelated
  refactors or cleanup.
- Every bug fix must include a regression test that fails before the fix and passes
  after it.
- After any code change, run the complete test suite with `python -m pytest -q`.
  Documentation-only changes do not require pytest unless the user requests it.
- Do not commit, push, or create a Pull Request without explicit user confirmation.
