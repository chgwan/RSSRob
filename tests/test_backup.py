import io
import zipfile

import pytest

from rssrob.backup import build_backup, restore_backup


def _tree(root):
    (root / "configs").mkdir()
    (root / "configs" / "a.yaml").write_text("name: a", encoding="utf-8")
    (root / "var").mkdir()
    (root / "var" / "feeds").mkdir()
    (root / "var" / "rssrob.db").write_bytes(b"\x00DB\xff")
    (root / "var" / "subscribers.json").write_text('{"x":1}', encoding="utf-8")


def test_build_backup_contains_files_and_manifest(tmp_path):
    _tree(tmp_path)
    data = build_backup(tmp_path, [tmp_path / "configs", tmp_path / "var"])
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert "configs/a.yaml" in names
    assert "var/rssrob.db" in names
    assert "var/subscribers.json" in names
    assert "manifest.json" in names


def test_build_backup_skips_missing_sources(tmp_path):
    _tree(tmp_path)
    data = build_backup(tmp_path, [tmp_path / "configs", tmp_path / "nope"])
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert "configs/a.yaml" in names  # missing source simply skipped, no error


def test_restore_roundtrip(tmp_path):
    src = tmp_path / "src"; src.mkdir(); _tree(src)
    data = build_backup(src, [src / "configs", src / "var"])
    dst = tmp_path / "dst"; dst.mkdir()
    restored = restore_backup(dst, data)
    assert (dst / "configs" / "a.yaml").read_text(encoding="utf-8") == "name: a"
    assert (dst / "var" / "rssrob.db").read_bytes() == b"\x00DB\xff"
    assert "configs/a.yaml" in restored and "var/rssrob.db" in restored


def test_restore_overwrites_existing(tmp_path):
    src = tmp_path / "src"; src.mkdir(); _tree(src)
    data = build_backup(src, [src / "configs"])
    dst = tmp_path / "dst"; dst.mkdir()
    (dst / "configs").mkdir()
    (dst / "configs" / "a.yaml").write_text("OLD", encoding="utf-8")
    restore_backup(dst, data)
    assert (dst / "configs" / "a.yaml").read_text(encoding="utf-8") == "name: a"


def test_restore_rejects_non_zip(tmp_path):
    with pytest.raises(ValueError):
        restore_backup(tmp_path, b"definitely not a zip")


def test_restore_rejects_zip_slip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(ValueError):
        restore_backup(tmp_path, buf.getvalue())


def test_restore_rejects_absolute_path(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/tmp/evil", "x")
    with pytest.raises(ValueError):
        restore_backup(tmp_path, buf.getvalue())
