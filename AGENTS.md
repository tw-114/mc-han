# mc-han Development Notes

This project is in phase 1. Keep changes focused on scanning and extraction unless the user asks for the next phase.

- Do not modify `mods/*.jar`; jar files are read-only inputs.
- Generated translations should eventually become resource-pack or config override outputs.
- JSON keys are identifiers and must not be translated.
- Base-name language keys such as `item.*`, `block.*`, `entity.*`, and `fluid.*` are intentionally skipped.
