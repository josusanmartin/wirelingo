"""The author's tailnet name for the Kria stays out of the tracked files. Docs
write `ubuntu@<kria-host>` and fpga/kv260/install.sh reads WL_KRIA instead
(fpga/kv260/README.md, "Build, install, test").

Every file `git ls-files` lists is read, evidence/ included: its redaction is
a manual search, with no test of its own for this name. test/mutants.py runs
each mutant in a copy of exactly those files with no .git, so there every
file in the copy is read. The needles are split so that this file does not
match itself.
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEEDLES = (b"kria.tail" b"[tailnet label redacted]", b"tail" b"[tailnet label redacted].ts.net")
NOT_SOURCE = {".git", ".venv", ".venv-flow", "__pycache__", "sim_build"}


def tracked(root):
    if (root / ".git").exists():
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
        return [n for n in out.decode().split("\0") if n]
    return [p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not NOT_SOURCE & set(p.relative_to(root).parts)]


def hits(root):
    """file:line for each needle in a tracked file (a listed path that is not a
    regular file, such as a submodule, is skipped)."""
    found = []
    for name in tracked(root):
        p = root / name
        if p.is_symlink() or not p.is_file():
            continue
        for n, line in enumerate(p.read_bytes().splitlines(), 1):
            if any(needle in line for needle in NEEDLES):
                found.append(f"{name}:{n}")
    return found


def test_no_tailnet_hostname_in_tracked_files():
    assert len(tracked(ROOT)) > 100
    assert not hits(ROOT), "the Kria's tailnet name is back; write ubuntu@<kria-host> or use WL_KRIA"


def test_a_planted_name_in_a_tracked_file_is_found(tmp_path):
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)
    git("init", "-q")
    host = (b"ubuntu@kria.tail" b"[tailnet label redacted].ts.net").decode()
    (tmp_path / "a.md").write_text(f"clean\nssh {host} uptime\n")
    (tmp_path / "b.sh").write_text("h=box.tail" "[tailnet label redacted].ts.net\n")
    (tmp_path / "untracked.md").write_text(host)
    git("add", "a.md", "b.sh")
    assert hits(tmp_path) == ["a.md:2", "b.sh:1"]
    (tmp_path / "a.md").write_text("clean\nssh ubuntu@<kria-host> uptime\n")
    (tmp_path / "b.sh").write_text("h=${WL_KRIA:?}\n")
    assert hits(tmp_path) == []
