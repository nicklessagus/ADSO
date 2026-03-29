"""Entry point: python -m adso."""

import logging
import os

_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _level, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# Silenciar librerías ruidosas
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.WARNING)

from adso.bot import run_bot  # noqa: E402

run_bot()
