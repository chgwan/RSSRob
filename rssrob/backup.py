"""Backup / restore RSSRob's runtime state + config as a single zip.

A backup bundles the active config and the runtime state (the SQLite item
history, generated feeds, subscriber list, digest state, 公众号 credential) so an
instance can be moved to another machine or rebuilt after a reinstall. Every file
is stored relative to a ``root`` directory, so a backup restores cleanly into a
fresh install at a different location.
"""

import io
import json
import time
import zipfile
from pathlib import Path

MANIFEST = "manifest.json"


def build_backup(root, sources) -> bytes:
    """Zip every file under each path in ``sources`` (all of which live under
    ``root``), naming entries relative to ``root``. Missing sources are skipped."""
    root = Path(root)
    buf = io.BytesIO()
    files = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sources:
            src = Path(src)
            if src.is_dir():
                members = (p for p in sorted(src.rglob("*")) if p.is_file())
            elif src.is_file():
                members = [src]
            else:
                continue
            for p in members:
                arc = p.relative_to(root).as_posix()
                zf.write(p, arc)
                files.append(arc)
        zf.writestr(MANIFEST, json.dumps(
            {"created_at": time.time(), "files": sorted(files)},
            ensure_ascii=False, indent=2))
    return buf.getvalue()


def _safe_target(root: Path, name: str) -> Path:
    """Resolve a zip member to a path strictly inside ``root`` (anti zip-slip)."""
    root_res = root.resolve()
    target = (root / name).resolve()
    if target == root_res or root_res not in target.parents:
        raise ValueError(f"unsafe path in backup: {name!r}")
    return target


def restore_backup(root, data: bytes) -> list:
    """Extract a backup zip into ``root``, overwriting existing files. Validates
    every entry first (rejecting absolute/escaping paths) and returns the list of
    restored entry names. Raises ``ValueError`` on a bad or unsafe archive."""
    root = Path(root)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a valid backup zip: {e}") from e

    members = [n for n in zf.namelist() if n != MANIFEST and not n.endswith("/")]
    targets = [(n, _safe_target(root, n)) for n in members]   # validate all first
    restored = []
    for name, target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src, open(target, "wb") as dst:
            dst.write(src.read())
        restored.append(name)
    return restored
