"""The author's tailnet name for the Kria stays out of the tracked files. Docs
write `"$WL_KRIA"` (or `<kria-host>` in prose) and fpga/kv260/install.sh reads
WL_KRIA instead (fpga/kv260/README.md, "Build, install, test").

Every file `git ls-files` lists is read, evidence/ included: its redaction is
a manual search, with no test of its own for this name. test/mutants.py runs
each mutant in a copy of exactly those files with no .git, so there every
file in the copy is read. The needle is the tailnet's own label, matched in
any case, so a Tailscale admin URL or an upper-case copy is caught too. It is
split so that this file does not match itself.

install.sh's refusal without a host is run on a scratch copy only: with no
host it stops before anything else, and ssh, scp and rsync are stubs that
fail loudly if it ever gets further.
"""
import os
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEEDLE = b"tail" b"[tailnet label redacted]"
NOT_SOURCE = {".git", ".venv", ".venv-flow", "__pycache__", "sim_build"}
INSTALL = ROOT / "fpga/kv260/install.sh"
REFUSAL = "set WL_KRIA to the board's ssh address, e.g. WL_KRIA=ubuntu@kria.example\n"


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
            if NEEDLE in line.lower():
                found.append(f"{name}:{n}")
    return found


def test_no_tailnet_hostname_in_tracked_files():
    assert len(tracked(ROOT)) > 100
    assert not hits(ROOT), "the Kria's tailnet name is back; write \"$WL_KRIA\" or <kria-host>"


def test_a_planted_name_in_a_tracked_file_is_found(tmp_path):
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)
    git("init", "-q")
    host = (b"ubuntu@kria.tail" b"[tailnet label redacted].ts.net").decode()
    (tmp_path / "a.md").write_text(f"clean\nssh {host} uptime\n")
    (tmp_path / "b.sh").write_text("h=box.tail" "[tailnet label redacted].ts.net\n")
    (tmp_path / "c.md").write_text("KRIA.TAIL" "[tailnet label redacted].TS.NET\n")
    (tmp_path / "d.md").write_text("the admin page of tailnet tail" "[tailnet label redacted]\n")
    (tmp_path / "untracked.md").write_text(host)
    git("add", "a.md", "b.sh", "c.md", "d.md")
    assert hits(tmp_path) == ["a.md:2", "b.sh:1", "c.md:1", "d.md:1"]
    (tmp_path / "a.md").write_text("clean\nssh \"$WL_KRIA\" uptime\n")
    for name in ("b.sh", "c.md", "d.md"):
        (tmp_path / name).write_text("h=${WL_KRIA:?}\n")
    assert hits(tmp_path) == []


def run_install(tmp_path, script, **env):
    """bash on a copy of install.sh in tmp_path, ssh/scp/rsync stubbed to fail."""
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    called = tmp_path / "called"
    for tool in ("ssh", "scp", "rsync"):
        (stub / tool).write_text(f"#!/bin/sh\necho 'STUB {tool} CALLED' >&2\necho {tool} >> '{called}'\nexit 97\n")
        (stub / tool).chmod(0o755)
    copy = tmp_path / "fpga/kv260/install.sh"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(script)
    base = {k: v for k, v in os.environ.items() if k not in ("WL_KRIA", "KRIA")}
    r = subprocess.run([shutil.which("bash"), str(copy)], cwd=tmp_path, capture_output=True, text=True,
                       env=dict(base, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}", **env))
    assert not called.exists(), called.read_text()
    return r


def test_install_refuses_with_no_host(tmp_path):
    src = INSTALL.read_text()
    for env in ({}, {"WL_KRIA": "", "KRIA": ""}):
        r = run_install(tmp_path, src, **env)
        assert (r.returncode, r.stdout, r.stderr) == (1, "", REFUSAL), env
    # cut just after the host check: which host it took, and that unset still stops there
    lines = src.splitlines(keepends=True)
    check = [i for i, line in enumerate(lines) if line.startswith('[ -n "$kria" ]')]
    assert len(check) == 1
    cut = "".join(lines[:check[0] + 1]) + 'printf "%s\\n" "$kria"\n'
    assert run_install(tmp_path, cut).stderr == REFUSAL
    assert run_install(tmp_path, cut, KRIA="a@old").stdout == "a@old\n"
    assert run_install(tmp_path, cut, KRIA="a@old", WL_KRIA="b@new").stdout == "b@new\n"
