"""Caché de parsing de notas keyed por (mtime, size).

Evita re-leer y re-parsear archivos .md sin cambios en scans repetidos del
vault. El costo dominante de un scan en la RPi4 (SD lenta) es el read()+parse
de cada nota, no el rglob; con un vault de 500+ notas, una captura corre
`get_all_tags` dos veces (escanea todo el vault), costando ~100-300 ms.

Correctness-preserving: la clave incluye mtime_ns + size, así que cualquier
modificación de una nota invalida su entrada automáticamente en el siguiente
stat(). No hace falta acoplarlo al VaultWatcher ni hay ventana de staleness.

Thread-safe: las funciones de scan corren dentro de `asyncio.to_thread`, así
que varios threads del pool pueden tocar el caché en paralelo.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import frontmatter

from adso.vault_writer import NoteData

logger = logging.getLogger(__name__)

# Cap de entradas para acotar memoria en la RPi4. Un vault personal típico
# tiene < 1000 notas; el LRU descarta las menos usadas si se excede.
_MAX_ENTRIES = 2000

_lock = threading.Lock()
# str(path) -> (mtime_ns, size, metadata, content)
_cache: "OrderedDict[str, tuple[int, int, dict, str]]" = OrderedDict()

# Métricas para /status.
_hits = 0
_misses = 0


def parse_cached(path: Path) -> Optional[NoteData]:
    """Parsea una nota usando caché por (mtime, size).

    Args:
        path: Path al archivo .md.

    Returns:
        NoteData, o None si el archivo no existe, no se puede leer, o no
        tiene frontmatter (mismo contrato que el parser directo).

    El frontmatter devuelto es siempre una copia fresca del dict cacheado,
    para que mutaciones del caller no corrompan el caché.
    """
    global _hits, _misses
    key = str(path)

    try:
        st = path.stat()
    except OSError:
        # Archivo borrado entre el rglob y el stat: limpiar entrada stale.
        with _lock:
            _cache.pop(key, None)
        return None

    mtime, size = st.st_mtime_ns, st.st_size

    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] == mtime and entry[1] == size:
            _cache.move_to_end(key)
            _hits += 1
            return NoteData(path=path, frontmatter=dict(entry[2]), body=entry[3])

    # Miss: leer y parsear fuera del lock (la I/O es lenta y no debe
    # serializar a los demás threads).
    try:
        raw = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
    except Exception:
        logger.debug("Error parsing nota: %s", path)
        return None

    if not post.metadata:
        return None

    meta = dict(post.metadata)
    content = post.content

    with _lock:
        _cache[key] = (mtime, size, meta, content)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)
        _misses += 1

    return NoteData(path=path, frontmatter=dict(meta), body=content)


def invalidate(path: Path) -> None:
    """Elimina una entrada del caché.

    No es estrictamente necesario (la clave por mtime se auto-invalida), pero
    es útil para tests y para forzar relectura tras una escritura.
    """
    with _lock:
        _cache.pop(str(path), None)


def clear() -> None:
    """Vacía el caché completo (usado en tests)."""
    global _hits, _misses
    with _lock:
        _cache.clear()
        _hits = 0
        _misses = 0


def stats() -> dict:
    """Métricas del caché para exponer en /status."""
    with _lock:
        total = _hits + _misses
        ratio = (_hits / total) if total else 0.0
        return {
            "entries": len(_cache),
            "hits": _hits,
            "misses": _misses,
            "hit_ratio": ratio,
        }
