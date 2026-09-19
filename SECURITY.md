# Security Policy

## Supported Versions

ADSO is a personal-use bot. Only the latest release line is supported.

| Version | Supported |
|---------|-----------|
| 1.9.x / `main` (latest) | Yes |
| < 1.9.0 (older releases) | No |

## Threat Model

ADSO is a single-user Telegram bot. The primary attack surface is:

- **Telegram messages**: Text, audio, images, documents, and URLs from the configured user.
- **Vault filesystem**: Notes written to and read from the Obsidian vault.
- **LLM API**: Content sent to Gemini and Groq for classification.
- **External URLs**: arXiv links only. The bot fetches metadata from the arXiv API (`arxiv_client.py`); there is no general web-page fetching or extraction. Any other URL is treated as plain text.
- **Git remote**: SSH push to the GitHub vault backup.

Authentication is enforced via `TELEGRAM_ALLOWED_USER_ID` — all other users are silently dropped. The bot is not intended for multi-user or public deployment.

## Reporting a Vulnerability

This is a personal project. If you find a security issue:

1. **Do not open a public GitHub issue** for sensitive vulnerabilities.
2. Email the maintainer or open a [GitHub Security Advisory](https://github.com/nicklessagus/ADSO/security/advisories/new).
3. Include: description, reproduction steps, and potential impact.

Response time: best-effort within 7 days for critical issues.

## Security Architecture

- All LLM input is wrapped in `<input>` tags with an explicit instruction to ignore embedded directives.
- The LLM always responds in a fixed JSON schema (Gemini constrained output); freeform text cannot escape the schema.
- No note is written to the vault without explicit user confirmation (inline keyboard) — with one documented exception: the inbox reclassification cron (`reclassify_inbox` in `handlers/jobs.py`) re-runs the LLM over an already-confirmed degraded note and writes the result with no preview and no injection check. See `docs/security.md` §8.
- Credentials live only in environment variables (`docker-compose.yml` loads them via `env_file: .env`; Docker secrets are not used) and in the OAuth JSON mounted at `/credentials` — never in code.
- Operations are restricted to an enumerated set (no arbitrary command execution).
- File uploads are saved to a sandboxed `03-Resources/` directory with sanitized filenames.
- See `docs/security.md` for the full threat model and mitigation checklist.

## Accepted Dependency Risks

Vulnerabilities that `pip-audit` reports but that are **not reachable in ADSO's
usage**, suppressed by ID in `.github/workflows/security.yml`. The suppression is
per-ID on purpose: a newly disclosed CVE is not hidden by it.

### chromadb 0.6.3 — CVE-2026-45830, CVE-2026-45831, CVE-2026-45833

All three require the ChromaDB **HTTP server** with authenticated, multi-tenant
users: a cross-tenant authorization bypass, a scope-blind
`SimpleRBACAuthorizationProvider`, and code injection through
`POST /api/v2/.../collections/{id}` with `trust_remote_code`.

ADSO embeds ChromaDB via `PersistentClient` against a local sqlite file
(`adso/embeddings.py`). There is no HTTP API, no tenants, no authorization
provider, and `docker-compose.yml` exposes no ports. None of the three attack
paths exists here.

The requirement is `chromadb~=0.5` (`requirements.txt`, `pyproject.toml`) — a
compatible-release range, not a pin to an exact version; 0.6.3 is what it
currently resolves to. No fixed version had been published at the time of
writing, so there is nothing newer to move to. **Revisit when a fixed chromadb
ships:** drop the `--ignore-vuln` flags, and tighten the requirement only if the
fix lands outside the `0.x` range that `~=0.5` already picks up.
