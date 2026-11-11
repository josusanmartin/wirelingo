"""No one's home directory in the tracked files: a builder's scratch path, a
local checkout's path or a log line can carry a user name (verifier 077,
finding 1: record_check.py and test_private_hosts.py don't look for these).

Every file `git ls-files` lists is read, evidence/ included (its verbatim
records too: a hit there is redacted by hand, as the evidence READMEs
describe); a tracked symlink's target is read as one line (git publishes
it); and each .py file's string constants are also read whole, as in
test_private_hosts.py, so adjacent literals can't split a path. The classes,
matched case-sensitively:

  a Linux home directory     `/home/` and a name (so `/var/home/<name>` and
                             `file:///home/<name>` too)
  a relative home path       `home/<name>/` not after a letter, digit or `/`,
                             as tar and rsync print paths
  a home path as a directory `-home-` and a name, not after a letter, digit
  name                       or `-`, the name ending at anything but a name
                             character: `-home-<name>` alone, before `/`, at
                             the end of a line, or before `-<more>` (the form
                             a session directory takes, `-home-<name>-dev-...`)
  a macOS home directory     `/Users/` and a name
  a Claude scratch path      `/tmp/claude-` and a uid

A home name passes only in the files ALLOWED gives for it: each entry is a
generic name public main already has, with its reason and the path prefixes
where it may appear. Anything under /Users/ or /tmp/claude- fails. `~/...`
and `$HOME/...` are not paths of anyone in particular and don't match. The
patterns are written split and the planted paths are made up at run time, so
this file doesn't match itself.

Not matched (verifier 078's table; none is in the tree today): an upper-case
`/HOME/`, JSON-escaped `\\/home\\/`, URL-encoded `%2Fhome%2F`, `C:\\Users\\...`,
`~<name>/`, a path inside a compressed (.gz) file or in UTF-16, a Python
string built with `+` (only adjacent literals are joined), the bare user name
(`<name>@host`), and `-home-<name>` right after a letter (`a-home-...`).
"""
import os
import pathlib
import re
import secrets
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_private_hosts import python_constant_lines, tracked  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
NAME = rb"([A-Za-z0-9_][A-Za-z0-9_.-]*)"
# (kind, pattern): "home" names may be allowed; the others always fail
CLASSES = [
    ("home", re.compile(rb"/ho" rb"me/" + NAME)),
    ("home", re.compile(rb"(?<![A-Za-z0-9/])ho" rb"me/" + NAME + rb"/")),
    ("home", re.compile(rb"(?<![A-Za-z0-9-])-ho" rb"me-([A-Za-z0-9_.]+)(?![A-Za-z0-9_.])")),
    ("users", re.compile(rb"/Us" rb"ers/" + NAME)),
    ("claude", re.compile(rb"/tmp/cla" rb"ude-[0-9]+")),
]
ALLOWED = {   # name: (reason, path prefixes where it may appear)
    b"ubuntu": ("the Kria's user (Ubuntu's default): board paths in verbatim logs and records",
                ("evidence/",)),
    b"runner1": ("the build host's account, in verbatim model logs and the verifier reports quoting them",
                 ("evidence/",)),
    b"pi": ("the Raspberry Pi's default user, in hw_day.py, its test and the verifier reports",
            ("fpga/kv260/host/hw_day.py", "test/model/test_hw_day.py", "evidence/verifier/")),
    b"vscode": ("the devcontainer's user", (".devcontainer/Dockerfile",)),
    b"someone": ("a made-up example user (verifier report 016 and the reports quoting it)",
                 ("evidence/verifier/",)),
    b"x": ("a made-up HOME in test_flow_tools.py", ("test/model/test_flow_tools.py",)),
}


def allowed(name, path):
    entry = ALLOWED.get(name)
    return entry is not None and path is not None and any(path.startswith(p) for p in entry[1])


def home_paths(text, path=None):
    """The matches in text that aren't a home name allowed in this tracked path (bytes)."""
    out = []
    for kind, pattern in CLASSES:
        for m in pattern.finditer(text):
            if kind == "home" and allowed(m.group(1), path):
                continue
            out.append(m.group(0))
    return out


def hits(root):
    """file:line for each home path in a tracked file, line 1 for a symlink's target
    (see the module doc)."""
    found = []
    for name in tracked(root):
        p = root / name
        if p.is_symlink():
            if home_paths(os.fsencode(os.readlink(p)), name):
                found.append(f"{name}:1")
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        lines = {n for n, line in enumerate(data.splitlines(), 1) if home_paths(line, name)}
        if name.endswith(".py"):
            lines |= python_constant_lines(data, lambda v: home_paths(v, name))
        found += [f"{name}:{n}" for n in sorted(lines)]
    return found


def test_no_home_path_in_tracked_files():
    assert len(tracked(ROOT)) > 100
    assert not hits(ROOT), "a home path is in a tracked file: write ~/..., $HOME/... or a placeholder"


def made_up_user():
    return "u" + secrets.token_hex(3)


def test_a_planted_home_path_is_found(tmp_path):
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)
    git("init", "-q")
    home, users, dashed = "/ho" + "me/", "/Us" + "ers/", "-ho" + "me-"
    planted = {
        "a.md": f"clean\nsee {home}{made_up_user()}/dev/protoemu/README.md\n",
        "b.log": "[check] from /tmp/cla" + "ude-1000/" + dashed + made_up_user() + "-proj/scratch/out3c\n",
        "c.md": "a scratch dir " + dashed + made_up_user() + "-dev-protoemu/\n",
        "d.sh": f"cd {users}{made_up_user()}/src\n",
        "e.txt": "/tmp/cla" + "ude-501/tasks/x.output\n",
        "f.md": f"HOME={home}{made_up_user()}\n",                        # no trailing slash
        "g.py": f'P = "{home[:3]}" "{home[3:]}{made_up_user()}/x"\n',   # adjacent literals, joined by Python
        "h.md": f"{home}{made_up_user().upper()}/x\n",                  # names are case-sensitive: not allowed
        # the home directory's own dashed name (verifier 078, finding 1)
        "i.md": "~/.claude/projects/" + dashed + made_up_user() + "/memory/MEMORY.md\n",
        "j.md": "the session directory is " + dashed + made_up_user() + "\n",       # at the end of a line
        "k.txt": "tar: " + "ho" + "me/" + made_up_user() + "/dev/x.txt: file changed\n",   # relative, as tar prints it
        "l.md": "home" + "/" + made_up_user() + "/dev\n",                         # relative, line start
        "m.md": f"{home}ubuntu/fstall/x.txt and {home}runner1/protoemu-flow\n",   # allowed in evidence/ only
    }
    link = f"{home}{made_up_user()}/dev/protoemu"                  # a tracked symlink: git publishes its target
    clean = {
        "clean.md": ("~/ttpdk and $HOME/ttpdk and ${HOME}/protoemu-flow\n"
                     "a home-made test, the -home- class as a word, --home-dir, /homework/, /tmp/claude-x/\n"
                     "privacy-home-path-default, a home/ directory, home/x (no slash after the name)\n"),
        "evidence/run/model.log": f"{home}ubuntu/fstall {home}runner1/protoemu-flow/test-venv\n",
        "evidence/verifier/016.md": f"{home}pi/hw_day.py and {home}someone/hw_day.py\n",
        ".devcontainer/Dockerfile": f"ENV PDK_ROOT={home}vscode/ttsetup/pdk\n",
        "test/model/test_flow_tools.py": f'HOME = "{home}x"\n',
    }
    for name, text in {**planted, **clean}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    os.symlink(link, tmp_path / "n.lnk")
    os.symlink("../README.md", tmp_path / "ok.lnk")
    (tmp_path / "untracked.md").write_text(planted["a.md"])
    git("add", "n.lnk", "ok.lnk", *planted, *clean)
    assert hits(tmp_path) == sorted(f"{name}:{2 if name == 'a.md' else 1}" for name in [*planted, "n.lnk"])
    for name in planted:
        (tmp_path / name).write_text("see ~/dev/protoemu and $HOME/ttpdk\n")
    (tmp_path / "n.lnk").unlink()
    os.symlink("../dev/protoemu", tmp_path / "n.lnk")
    assert hits(tmp_path) == []


def test_the_allowlist_is_only_generic_names_in_their_files():
    """Each allowed name has its reason and its files, and the scan is not vacuous on
    them: a name beside an allowed one (ubuntu2) or containing it (piotr) is not
    allowed, nor an allowed name outside its files (runner1 outside evidence/)."""
    assert all(reason and prefixes for reason, prefixes in ALLOWED.values())
    home, dashed = b"/ho" + b"me/", b"-ho" + b"me-"
    ev = "evidence/fabric-ping-20261103/c108e09-suite/model.log"
    assert home_paths(home + b"ubuntu/x", ev) == [] and home_paths(home + b"runner1", ev) == []
    assert home_paths(home + b"runner1/x", "fpga/kv260/build.sh")
    assert home_paths(home + b"runner1/x", "docs/hardware-day.md") and home_paths(home + b"runner1/x")
    assert home_paths(home + b"ubuntu/x", "fpga/kv260/host/pe_kria.py")
    assert home_paths(home + b"pi/x", "fpga/kv260/host/hw_day.py") == []
    assert home_paths(home + b"pi/x", "fpga/kv260/host/pe_kria.py")
    assert home_paths(home + b"ubuntu2/x", ev) and home_paths(home + b"piotr/x", "evidence/verifier/x.md")
    assert home_paths(dashed + b"ubuntu-", ev) == [] and home_paths(dashed + b"ubuntua", ev)
    assert home_paths(b"/Us" + b"ers/pi/x", "evidence/verifier/x.md")
    assert home_paths(b"/tmp/cla" + b"ude-0/x", ev)
