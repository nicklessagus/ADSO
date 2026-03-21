"""Entry point: python -m adso."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

from adso.bot import run_bot  # noqa: E402

run_bot()
