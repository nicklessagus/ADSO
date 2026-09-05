#!/usr/bin/env python3
"""Reconcile the ChromaDB index against the notes on disk. Read-only.

`VaultWatcher` keeps the index in sync with external edits: an edit in Obsidian
re-embeds the note, a deletion removes its embedding. This script answers the
question that mechanism raises — *did it actually work?* — by diffing the two
sides, which is the only way to tell a working watcher from a silent one.

Run it inside the container, where `/vault` and the Chroma volume are mounted::

    make check-sync

Two rules the diff depends on, both taken from the bot rather than restated
here (restating them produces false positives — `_index.md` files look like 5
missing embeddings until you notice the bot skips them on purpose):

- what belongs in the index is `embeddings.should_index` (`exclude_dirs`,
  `_index.md`, Syncthing conflicts);
- the document id is the note's vault-relative path without the suffix.

Exit code is 0 when both sides agree, 1 when they do not, so it can gate a
deploy or a cron.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import chromadb

from adso.constants import DEFAULT_EXCLUDE_DIRS
from adso.embeddings import COLLECTION_NAME, should_index

EXCLUDE_DIRS = list(DEFAULT_EXCLUDE_DIRS)


def main() -> int:
    vault = Path(os.environ.get("VAULT_PATH", "/vault"))
    chroma_dir = os.environ.get("CHROMA_DATA_DIR", "/app/data/chroma")

    on_disk = {
        str(p.relative_to(vault).with_suffix(""))
        for p in vault.rglob("*.md")
        if should_index(p, vault, EXCLUDE_DIRS)
    }
    collection = chromadb.PersistentClient(path=chroma_dir).get_or_create_collection(
        name=COLLECTION_NAME
    )
    indexed = set(collection.get(include=[])["ids"])

    stale = sorted(indexed - on_disk)      # indexed, but the note is gone
    missing = sorted(on_disk - indexed)    # on disk, never indexed

    print(f"indexable notes on disk : {len(on_disk)}")
    print(f"embeddings in ChromaDB  : {len(indexed)}\n")
    for label, items in (("indexed but gone from disk", stale), ("on disk but not indexed", missing)):
        print(f"--- {label}: {len(items)}")
        for item in items[:25]:
            print("   ", item)
        if len(items) > 25:
            print(f"    … and {len(items) - 25} more")
    print()

    if stale or missing:
        print("OUT OF SYNC — the nightly reindex job reconciles both directions.")
        return 1
    print("OK — index and disk agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
