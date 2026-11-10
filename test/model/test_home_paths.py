"""No one's home directory in the tracked files: a builder's scratch path, a
local checkout's path or a log line can carry a user name (verifier 077,
finding 1: record_check.py and test_private_hosts.py don't look for these).

Every file `git ls-files` lists is read, evidence/ included (its verbatim
records too: a hit there is redacted by hand, as the evidence READMEs
describe), and each .py file's string constants are also read whole, as in
test_private_hosts.py, so adjacent literals can't split a path. The classes,
matched case-sensitively:

  a Linux home directory    `/home/` and a name
  a scratch directory name  `-home-` and a name and `-`, the form a home path
                            takes as one directory's name (`-home-<name>-...`)
  a macOS home directory    `/Users/` and a name
  a Claude scratch path     `/tmp/claude-` and a uid

A /home name passes only if it is one of ALLOWED, the generic names public
main already has, each with its reason; anything under /Users/ or
/tmp/claude- fails. `~/...` and `$HOME/...` are not paths of anyone in
particular and don't match. The patterns are written split and the planted
paths are made up at run time, so this file doesn't match itself.
"""
import pathlib
import re
import secrets
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_private_hosts import python_constant_lines, tracked  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
NAME = rb"([A-Za-z0-9_][A-Za-z0-9_.-]*)"
HOME = re.compile(rb"/ho" rb"me/" + NAME
                  + rb"|(?<![A-Za-z0-9])-ho" rb"me-([A-Za-z0-9_.]+)-"
                  + rb"|/Us" rb"ers/" + NAME
                  + rb"|/tmp/cla" rb"ude-[0-9]+")
ALLOWED = {
    b"ubuntu": "the Kria's user (Ubuntu's default): board paths in logs and runbooks",
    b"pi": "the Raspberry Pi's default user, in hw_day.py and the runbook's examples",
    b"vscode": "the devcontainer's user (.devcontainer/Dockerfile)",
    b"runner1": "the build host's account, in verbatim model logs and the verifier reports quoting them",
    b"someone": "a made-up example user (verifier report 016 and the README quoting it)",
    b"x": "a made-up HOME in test_flow_tools.py",
}


def home_paths(text):
    """The matches in text that aren't an allowed /home name (bytes)."""
    out = []
    for m in HOME.finditer(text):
        name = m.group(1) or m.group(2)
        if m.group(0).startswith(b"/ho" b"me/") or m.group(0).startswith(b"-ho" b"me-"):
            if name in ALLOWED:
                continue
        out.append(m.group(0))
    return out


def hits(root):
    """file:line for each home path in a tracked file (see the module doc)."""
    found = []
    for name in tracked(root):
        p = root / name
        if p.is_symlink() or not p.is_file():
            continue
        data = p.read_bytes()
        lines = {n for n, line in enumerate(data.splitlines(), 1) if home_paths(line)}
        if name.endswith(".py"):
            lines |= python_constant_lines(data, home_paths)
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
    home, users = "/ho" + "me/", "/Us" + "ers/"
    planted = {
        "a.md": f"clean\nsee {home}{made_up_user()}/dev/protoemu/README.md\n",
        "b.log": "[check] from /tmp/cla" + "ude-1000/-ho" + "me-" + made_up_user() + "-proj/scratch/out3c\n",
        "c.md": "a scratch dir -ho" + "me-" + made_up_user() + "-dev-protoemu/\n",
        "d.sh": f"cd {users}{made_up_user()}/src\n",
        "e.txt": "/tmp/cla" + "ude-501/tasks/x.output\n",
        "f.md": f"HOME={home}{made_up_user()}\n",                        # no trailing slash
        "g.py": f'P = "{home[:3]}" "{home[3:]}{made_up_user()}/x"\n',   # adjacent literals, joined by Python
        "h.md": f"{home}{made_up_user().upper()}/x\n",                  # names are case-sensitive: not allowed
    }
    clean = ("~/ttpdk and $HOME/ttpdk and ${HOME}/protoemu-flow\n"
             f"{home}ubuntu/protoemu48 {home}pi/hw_day.py {home}vscode/ttsetup {home}runner1/protoemu-flow\n"
             "a home-made test, the -home- class as a word, /homework/, /tmp/claude-x/\n")
    for name, text in planted.items():
        (tmp_path / name).write_text(text)
    (tmp_path / "clean.md").write_text(clean)
    (tmp_path / "untracked.md").write_text(planted["a.md"])
    git("add", "clean.md", *planted)
    assert hits(tmp_path) == [f"{name}:{2 if name == 'a.md' else 1}" for name in sorted(planted)]
    for name in planted:
        (tmp_path / name).write_text("see ~/dev/protoemu and $HOME/ttpdk\n")
    assert hits(tmp_path) == []


def test_the_allowlist_is_only_generic_names():
    """Each allowed name has its reason, and the scan is not vacuous on them: a name
    beside an allowed one (ubuntu2) or containing it (piotr) is not allowed."""
    assert all(reason for reason in ALLOWED.values())
    home = b"/ho" b"me/"
    assert home_paths(home + b"ubuntu/x") == [] and home_paths(home + b"ubuntu") == []
    assert home_paths(home + b"ubuntu2/x") and home_paths(home + b"piotr/x")
    dashed = b"-ho" + b"me-"
    assert home_paths(dashed + b"pi-") == [] and home_paths(dashed + b"pia-")
    assert home_paths(b"/Us" + b"ers/pi/x") and home_paths(b"/tmp/cla" + b"ude-0/x")
