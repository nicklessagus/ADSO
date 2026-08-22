"""Configuración del logging del proceso.

Vive fuera de `__main__.py` para que sea testeable: importar `__main__` arranca
el bot, así que la config que vivía ahí no se podía verificar sin efectos.
"""

from __future__ import annotations

import logging
import os

#: Loggers de librerías que emiten INFO por operación normal y tapan el log del
#: bot. `apscheduler.executors.default` es el peor: dos líneas por corrida de
#: job, y el `heartbeat_job` corre cada 60s — 2880 de las 3001 líneas de un día
#: (96%) eran eso. Se silencia el executor, **no** `apscheduler` entero: el
#: logger del scheduler avisa arranque y, sobre todo, "Run time of job was
#: missed", que es la señal de que el event loop se está bloqueando.
_LIBRERIAS_RUIDOSAS = (
    "httpx",
    "telegram",
    "chromadb",
    "googleapiclient.discovery_cache",
    "apscheduler.executors.default",
)


def configure_logging() -> None:
    """Configura el logging raíz desde `LOG_LEVEL` y silencia las librerías ruidosas.

    Idempotente en la práctica: `basicConfig` no hace nada si el root ya tiene
    handlers, y los `setLevel` son asignaciones.

    Comportamiento ante error: un `LOG_LEVEL` inválido no aborta el arranque —
    cae a INFO.
    """
    nivel_env = os.environ.get("LOG_LEVEL", "INFO").upper()
    nivel = getattr(logging, nivel_env, logging.INFO)
    if not isinstance(nivel, int):  # p.ej. LOG_LEVEL=getLogger
        nivel = logging.INFO

    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logging.getLogger().setLevel(nivel)

    for nombre in _LIBRERIAS_RUIDOSAS:
        logging.getLogger(nombre).setLevel(logging.WARNING)

    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
