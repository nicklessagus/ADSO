## Summary

<!-- 1-3 bullet points describing what this PR does and why. -->

-
-

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Security fix
- [ ] Refactor (no behavior change)
- [ ] Documentation
- [ ] Dependency update

## Testing

- [ ] `pytest` passes locally (`pytest --cov=adso`)
- [ ] Coverage stays above 80%
- [ ] New behavior is covered by tests
- [ ] Tested on Raspberry Pi 4 / Docker (if infrastructure change)

## Security checklist (required for any PR touching LLM or vault I/O)

- [ ] User-supplied content is wrapped in `<input>` tags before LLM calls
- [ ] File paths derived from user input use `Path(...).name` (no path traversal)
- [ ] No credentials or secrets added to code or config examples
- [ ] No new blocking I/O on the async event loop

## Docs updated?

- [ ] `docs/` updated if behavior or APIs changed
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`
- [ ] `CLAUDE.md` updated if conventions or design decisions changed

## Breaking changes

<!-- List any breaking changes to config.yaml schema, frontmatter fields, or module APIs. -->

None / describe below:
