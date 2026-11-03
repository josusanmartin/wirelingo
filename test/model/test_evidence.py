"""evidence/kv260-usb-20261030/ is the archive's files, byte for byte, apart from
the redactions listed in its manifest.json.

Two checks:
* every committed file's sha256 equals the manifest's (always runs: it catches a
  checkout or copy step that changes line endings or bytes);
* rebuilt from the archive with the listed redactions applied, line by line, each
  file equals the committed one. This needs the archive, which is not in git: set
  WIRELINGO_USB_ARCHIVE to its path (default ~/wirelingo-usb-real-host-20261030.tar.gz),
  or the check skips.

The manifest names each redaction by file, line and kind, never the value removed.
`python3 test/model/test_evidence.py [ARCHIVE]` runs both checks without pytest.
"""
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
USB = ROOT / "evidence" / "kv260-usb-20261030"
KINDS = {
    "email": (re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
              b"[email redacted]"),
    "mac": (re.compile(rb"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"), b"[MAC redacted]"),
}


def manifest():
    return json.loads((USB / "manifest.json").read_text())


def sha(b):
    return hashlib.sha256(b).hexdigest()


def redact(name, data, redactions):
    """Apply the manifest's redactions for one file; each must replace exactly once."""
    lines = data.splitlines(keepends=True)
    for r in redactions:
        if r["file"] != name:
            continue
        rx, rep = KINDS[r["kind"]]
        new, n = rx.subn(rep, lines[r["line"] - 1])
        assert n == 1, (name, r["line"], r["kind"], n)
        lines[r["line"] - 1] = new
    return b"".join(lines)


def check_committed():
    m = manifest()
    on_disk = sorted(p.relative_to(USB).as_posix() for p in USB.rglob("*")
                     if p.is_file() and p.name not in ("README.md", "manifest.json"))
    assert on_disk == sorted(m["files"]), (on_disk, sorted(m["files"]))
    for name, h in m["files"].items():
        assert sha((USB / name).read_bytes()) == h["committed"], name


def check_archive(path):
    m = manifest()
    raw = pathlib.Path(path).read_bytes()
    assert sha(raw) == m["archive_sha256"]
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
        members = {i.name: t.extractfile(i).read() for i in t.getmembers() if i.isfile()}
    assert sorted(members) == sorted(m["files"])
    for name, data in members.items():
        assert sha(data) == m["files"][name]["original"], name
        assert redact(name, data, m["redactions"]) == (USB / name).read_bytes(), name


def archive_path():
    return os.environ.get("WIRELINGO_USB_ARCHIVE",
                          os.path.expanduser("~/wirelingo-usb-real-host-20261030.tar.gz"))


def test_usb_logs_are_the_manifest_bytes():
    check_committed()


def test_usb_logs_equal_the_archive_apart_from_the_listed_redactions():
    import pytest
    if not os.path.exists(archive_path()):
        pytest.skip("archive not here: set WIRELINGO_USB_ARCHIVE")
    check_archive(archive_path())


def test_no_unlisted_email_or_mac_is_left():
    m = manifest()
    for name in m["files"]:
        data = (USB / name).read_bytes()
        for kind, (rx, _) in KINDS.items():
            hits = [x for x in rx.findall(data) if not x.endswith((b".service", b".path"))]
            assert not hits, (name, kind)


if __name__ == "__main__":
    check_committed()
    print("committed files: manifest sha256 equal")
    p = sys.argv[1] if len(sys.argv) > 1 else archive_path()
    check_archive(p)
    print("archive: equal apart from the listed redactions")
