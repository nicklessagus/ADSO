"""Shared test helpers, imported as ``from tests.helpers import ...``.

Kept out of ``conftest.py`` on purpose: these are plain functions, not
fixtures, so a test can call them without threading a parameter through.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter as fm_lib

_DEFAULTS = {"title": "Nota", "type": "reference", "status": "active"}


def write_note(path: Path, body: str = "", *, defaults: bool = True, **fm) -> Path:
    """Write a ``.md`` note with YAML frontmatter, creating parent directories.

    Args:
        path: Destination file.
        body: Note body.
        defaults: Fill in ``title``/``type``/``status`` when the caller does
            not provide them. Pass ``False`` to write exactly ``fm``.
        **fm: Frontmatter fields.

    Returns:
        ``path``, for chaining.
    """
    meta = dict(_DEFAULTS) if defaults else {}
    meta.update(fm)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm_lib.dumps(fm_lib.Post(body, **meta)), encoding="utf-8")
    return path
