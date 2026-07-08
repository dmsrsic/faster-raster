# CLI UX Design

FasterRaster uses a read-first command-line product surface. Standard commands are direct and professional. Kitchen Mode adds a branded vocabulary for exploratory work without mutating schemas or JSON contracts.

Design rules:

- Plain output is ANSI-free.
- JSON output is stable and parseable.
- Styled output may use Rich tables and panels.
- Network probes are dry-run by default and require explicit opt-in for live access.
- Auth profile output is redacted and shows environment variable names only.
- Blocked sources are planning states, not product failures.

Kitchen Mode appears in headings, aliases, explore mode, and demo screenshots. It does not affect runtime source registry behavior or acquisition planning.
