"""Entry point: python -m adso."""

from adso.logging_setup import configure_logging

configure_logging()

from adso.bot import run_bot  # noqa: E402

run_bot()
