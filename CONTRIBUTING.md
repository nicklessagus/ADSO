# Contributing

ADSO is a personal project, but contributions, issues, and discussion are welcome.

## Development Setup

```bash
git clone https://github.com/nicklessagus/ADSO.git
cd ADSO
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python ≥ 3.11. No native dependencies beyond what pip installs.

To run the bot locally (not just tests), create `.env` and `config.yaml`:

```bash
cp .env.example .env        # fill in your API keys
cp config.yaml.example config.yaml
python -m adso
```

## Running Tests

`adso/security.py` validates `TELEGRAM_ALLOWED_USER_ID` at import time, so pytest needs dummy env vars (same values CI uses):

```bash
export TELEGRAM_ALLOWED_USER_ID=12345
export TELEGRAM_TOKEN=dummy
export GEMINI_API_KEY=dummy
```

```bash
pytest                        # full suite
pytest tests/unit/            # unit tests only
pytest -m "not integration"   # skip integration tests
pytest --cov=adso --cov-report=term-missing
```

Coverage threshold is 70% (CI gate, logic modules only — see `docs/testing.md`). Tests must pass before any PR is merged.

## Code Conventions

- **Async everywhere**: all I/O must use `async/await`. No blocking calls on the event loop.
- **Type hints on all function signatures** (including return types).
- **Docstrings on public functions**: describe behavior, args, and error conditions.
- **Single responsibility**: `bot.py` orchestrates; modules process. No cross-cutting concerns.
- **No silent exceptions**: catch, log, and surface errors to the user. Never swallow them.
- **Validation at boundaries**: validate all external input (Telegram, LLM responses, filesystem).
  Internal data structures are trusted once validated.
- **Security**: wrap all user-supplied content in `<input>` tags before LLM calls. Never interpolate
  raw user input into system prompts.

## Commit Messages

Follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
type(scope): short description

feat(capture): add link deduplication for arXiv papers
fix(vault): sanitize filename in save_resource
docs(security): update threat model with SSRF mitigations
refactor(llm): consolidate retry logic
test(embeddings): add integration test for reindex_vault
chore(docker): upgrade Python base image to 3.11-slim-bookworm
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `style`, `perf`, `security`, `ci`, `build`

## Pull Request Process

1. Fork the repository and create a branch from `main`.
2. Add tests for any new behavior or bug fix.
3. Run `pytest` and verify all tests pass.
4. Update `docs/` if you change behavior or APIs.
5. Open a PR with a clear description of the change and why.
6. Tag the PR with the relevant label (bug, enhancement, security, documentation).

## Scope

This is a personal knowledge management bot. PRs that add:
- New capture pipelines (new media types, external services)
- Vault management improvements
- Performance on ARM64 (Raspberry Pi 4)
- Security hardening

...are most likely to be accepted. Large architectural changes require prior discussion in an issue.

## Security Issues

Do not open public issues for security vulnerabilities. See [SECURITY.md](SECURITY.md).
