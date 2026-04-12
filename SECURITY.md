# Security Policy

## Supported Versions

ADSO is a personal-use bot. Only the latest commit on `main` is supported.

| Version | Supported |
|---------|-----------|
| `main` (latest) | Yes |
| Older commits | No |

## Threat Model

ADSO is a single-user Telegram bot. The primary attack surface is:

- **Telegram messages**: Text, audio, images, documents, and URLs from the configured user.
- **Vault filesystem**: Notes written to and read from the Obsidian vault.
- **LLM API**: Content sent to Gemini and Groq for classification.
- **External URLs**: Links processed for web content extraction.
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
- No note is written to the vault without explicit user confirmation (inline keyboard).
- Credentials live only in environment variables and Docker secrets — never in code.
- Operations are restricted to an enumerated set (no arbitrary command execution).
- File uploads are saved to a sandboxed `03-Resources/` directory with sanitized filenames.
- See `docs/security.md` for the full threat model and mitigation checklist.
