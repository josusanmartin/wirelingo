"""No tailnet name or address in the tracked files, the author's Kria
included. Docs write `"$WL_KRIA"` (or `<kria-host>` in prose) and
fpga/kv260/install.sh reads WL_KRIA instead (fpga/kv260/README.md, "Build,
install, test").

Every file `git ls-files` lists is read, evidence/ included: its redaction is
a manual search, with no test of its own for these names. test/mutants.py runs
each mutant in a copy of exactly those files with no .git, so there every
file in the copy is read.

The search is for the class, not for one name, so no private name is written
in this repository in any form, split or encoded. (A hash would not hide it:
a label of the form below has 2**24 values, all quick to try.) Each line is
matched in lower case, so a Tailscale admin URL or an upper-case copy is
caught too:

  a tailnet label         `tail` and four hex digits or more, alone or in a name
  a MagicDNS name         any `<label>.ts.net`
  a tailnet IPv4 address  100.64.0.0/10, except the range's base 100.64.0.0,
                          which the evidence READMEs quote as the class searched for
  a tailnet IPv6 address  Tailscale's /48, fd7a:115c:a1e0 then `::`

`*.ts.net` and `TAIL…TS.NET`, the class as the redaction notes write it, are
not names and do not match. The planted names below are made up at run time,
and each pattern is written so that this file does not match itself.

install.sh's refusal without a host is run on a scratch copy only: with no
host it stops before anything else, and ssh, scp and rsync are stubs that
fail loudly if it ever gets further.
"""
import os
import pathlib
import re
import secrets
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
TAILNET = re.compile(rb"tail[0-9a-f]{4}"
                     rb"|[a-z0-9_-]\.ts\.net"
                     rb"|(?<![0-9.])100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}(?![0-9])"
                     rb"|fd7a:115c:a1e0[:]")
CLASS_ONLY = {b"100.64.0.0"}
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
    """file:line for each tailnet name or address in a tracked file (a listed
    path that is not a regular file, such as a submodule, is skipped)."""
    found = []
    for name in tracked(root):
        p = root / name
        if p.is_symlink() or not p.is_file():
            continue
        for n, line in enumerate(p.read_bytes().splitlines(), 1):
            if any(m.group() not in CLASS_ONLY for m in TAILNET.finditer(line.lower())):
                found.append(f"{name}:{n}")
    return found


def test_no_tailnet_name_or_address_in_tracked_files():
    assert len(tracked(ROOT)) > 100
    assert not hits(ROOT), "a tailnet name or address is in a tracked file; write \"$WL_KRIA\" or <kria-host>"


def made_up_label():
    return "tail" + secrets.token_hex(3)


def test_a_planted_name_in_a_tracked_file_is_found(tmp_path):
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)
    git("init", "-q")
    label = made_up_label()
    host = "ubuntu@kria." + label + ".ts" + ".net"
    planted = {
        "a.md": f"clean\nssh {host} uptime\n",
        "b.sh": "h=box." + made_up_label() + ".ts" + ".net\n",
        "c.md": host.upper() + "\n",
        "d.md": f"the admin page of tailnet {label}\n",
        "e.md": "ssh kria.wren-otter.ts" + ".net\n",
        "f.md": "?tailnet=" + made_up_label().upper() + "\n",
        "g.sh": "WL_KRIA=ubuntu@100.10" + "1.102.103\n",
        "h.md": "ends of the range 100.6" + "4.0.1 and 100.12" + "7.255.254\n",
        "i.md": "ping FD7A:115C:A1E0" + "::53\n",
    }
    # the class as the redaction notes name it, and near misses
    clean = ("searched for `*.ts.net`, 100.64.0.0/10 and `TAIL…TS.NET`\n"
             "tail -f m.log; tailored tails; detail\n"
             "100.63.255.1 100.128.0.1 10.100.64.1.5 v1.100.70.2 1100.64.1.1 192.168.1.1\n")
    for name, text in planted.items():
        (tmp_path / name).write_text(text)
    (tmp_path / "clean.md").write_text(clean)
    (tmp_path / "untracked.md").write_text(host)
    git("add", "clean.md", *planted)
    assert hits(tmp_path) == [f"{name}:{2 if name == 'a.md' else 1}" for name in sorted(planted)]
    (tmp_path / "a.md").write_text("clean\nssh \"$WL_KRIA\" uptime\n")
    for name in planted:
        if name != "a.md":
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
