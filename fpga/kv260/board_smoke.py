#!/usr/bin/env python3
"""Load a protoemu app on the Kria and run the standard bring-up and protocol
smoke tests on it, from another machine (the orchestrator's; never a builder's):

    export WL_KRIA=ubuntu@<kria-host>
    python3 fpga/kv260/board_smoke.py [--app protoemu3c] [--out-dir DIR] [--phases install,load,smoke]
                                      [--expect REV] [--log FILE] [--timeout S]

For the 3-core build: `OUT_DIR=DIR fpga/kv260/build.sh --cores 3`, then this
with `--out-dir DIR` (default fpga/kv260/out). Phases, in order; any refusal
stops everything after it:

check    (always, local) DIR/build_info.json is the build --app holds
         (fabric_guard.APP_BUILD: 3 cores, clk_div 24 for protoemu3c) and comes
         from this checkout's RTL, not dirty.
board    (always, read-only, before anything reaches the board) `uptime`,
         then `pgrep -af 'xmutil|fpga/kv260/host/'`: nothing of ours running
         (one process at a time).
install  `LOAD=0 APP=<app> OUT_DIR=DIR fpga/kv260/install.sh`: copies the
         bitstream, overlay and host software (~/<app>), loads nothing.
load     read-only first: `sudo xmutil listapps` printing its table (the app
         must be in it). Then the load,
         one ssh command each, each output printed and read before the next:
         `sudo xmutil unloadapp` (stop on a non-zero status or any error line),
         `sudo xmutil loadapp <app>` (the same; xmutil can print Load Error and
         exit 0), `sudo xmutil listapps` (fabric_guard's strict parser: <app>
         must be the one active row). Anything else: nothing runs, see
         docs/hardware-day.md step 0.
smoke    on the board, one at a time from ~/<app>, each to its end before the
         next: pmod_state.py (every PMOD pin disabled), bringup_kria.py (the
         core count from the app), spim_test.py, v2_demo.py call la spi i2c,
         pmod_state.py again. A test passes on status 0 and its ALL PASS line;
         the first failure stops the run. None of them enables a PMOD pin or
         touches eth0; nothing here reboots or power-cycles anything.

Exit status: 0 all passed, 1 a test failed, 2 refused (nothing after it ran).
--phases smoke alone runs the tests on an app already loaded (the load phase's
listapps check still runs first, read-only); --phases install alone stops after
the read-only table, exit 0, nothing loaded. The record (--log) is the
printed output, verbatim.
"""
import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "host"))
import app_build  # noqa: E402
import fabric_guard  # noqa: E402

PHASES = ("install", "load", "smoke")
ERROR = re.compile(r"error|fail", re.I)
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
TOOLS = "fpga/kv260/host/"
BUSY = f"xmutil|{TOOLS}"                  # pgrep -f pattern: a load in progress or a board tool running


class Refused(Exception):
    """A safety check stopped the run: nothing after it ran."""


class Failed(Exception):
    """A test on the board failed: the run stopped there."""


def tests(app):
    """(name, remote command run from ~/<app>, needs an ALL PASS line)."""
    py = f"sudo python3 {TOOLS}"
    return [("pmod before", py + "pmod_state.py", False),
            ("bringup", py + "bringup_kria.py", True),
            ("spim", py + "spim_test.py", True),
            ("v2 demos", py + "v2_demo.py call la spi i2c", True),
            ("pmod after", py + "pmod_state.py", False)]


class Ssh:
    """One remote command per call, its output as text. The test stand-in has the same run()."""

    def __init__(self, kria):
        self.kria = kria

    def run(self, cmd, timeout):
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", self.kria, cmd], capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return None, out
        return r.returncode, r.stdout + r.stderr


class Smoke:
    def __init__(self, a, remote, local=subprocess.run, say=print):
        self.a, self.remote, self.local, self.say = a, remote, local, say
        self.results = []

    # -- helpers
    def remote_cmd(self, cmd, timeout=60):
        self.say(f"$ {cmd}")
        rc, out = self.remote.run(cmd, timeout)
        for line in out.splitlines():
            self.say(f"  | {line}")
        if rc is None:
            raise Refused(f"`{cmd}` didn't finish in {timeout} s; it may still be running on the board: "
                          f"check with `pgrep -af '{BUSY}'` (read-only) before anything else")
        self.say(f"  (status {rc})")
        return rc, out

    # -- phases
    def checkout_info(self):
        return json.loads(subprocess.run([sys.executable, str(HERE / "build_id.py"), "info"], capture_output=True,
                                         text=True, check=True).stdout)

    def check(self):
        a = self.a
        info = self.checkout_info()
        rec = pathlib.Path(a.out_dir) / "build_info.json"
        if not rec.is_file():
            raise Refused(f"{rec} is missing: build it first (OUT_DIR={a.out_dir} fpga/kv260/build.sh "
                          f"--cores {fabric_guard.APP_BUILD.get(a.app, ('?',))[0]})")
        built = json.loads(rec.read_text())
        try:
            for line in app_build.check(a.app, info, built):
                self.say(line)
        except SystemExit as e:
            raise Refused(str(e))
        if built.get("rtl_rev") != info["rtl_rev"] or info["dirty"] or built.get("dirty"):
            raise Refused("the bitstream isn't this checkout's clean RTL: bringup_kria.py's build step would fail")
        if "install" in a.phases and not (pathlib.Path(a.out_dir) / "protoemu.bit.bin").is_file():
            raise Refused(f"{a.out_dir}/protoemu.bit.bin is missing (built with FETCH=0? fetch it first)")

    def install(self):
        env = dict(os.environ, LOAD="0", APP=self.a.app, OUT_DIR=str(self.a.out_dir), WL_KRIA=self.a.kria)
        self.say(f"$ LOAD=0 APP={self.a.app} OUT_DIR={self.a.out_dir} fpga/kv260/install.sh")
        r = self.local([str(HERE / "install.sh")], env=env, capture_output=True, text=True)
        for line in (r.stdout + r.stderr).splitlines():
            self.say(f"  | {line}")
        if r.returncode:
            raise Refused(f"install.sh failed (status {r.returncode}): nothing was loaded")
        if f"installed {self.a.app} (not loaded)" not in r.stdout:
            raise Refused("install.sh's output doesn't say the app was installed and not loaded: stop and read it")

    def alive(self):
        """Before anything reaches the board (install included): it answers, and nothing of
        ours (a board tool, an xmutil command) is running on it: one process at a time."""
        rc, _ = self.remote_cmd("uptime")
        if rc != 0:
            raise Refused("the Kria doesn't answer: docs/hardware-day.md step 0 (recover); never power-cycle "
                          "a board that answers")
        rc, out = self.remote_cmd(f"pgrep -af {shlex.quote(BUSY)} || true")
        mine = [ln for ln in out.splitlines() if ("xmutil" in ln or TOOLS in ln) and "pgrep" not in ln]
        if rc != 0 or mine:
            raise Refused(f"a board tool or xmutil is already running ({len(mine)} line(s) above): "
                          f"one process at a time")

    def preflight(self):
        rc, out = self.remote_cmd("sudo xmutil listapps")
        if rc != 0:
            raise Refused(f"`xmutil listapps` failed: {fabric_guard.STOP}")
        try:
            active = fabric_guard.parse(out)
        except fabric_guard.NoActiveApp as e:
            raise Refused(str(e))
        names = [n for n, _ in fabric_guard.rows(out)]
        if self.a.app not in names:
            raise Refused(f"{self.a.app} isn't installed on the board (not in the table): run the install phase")
        self.say(f"  active now: {active or 'none'}")
        return active

    def load(self):
        app = self.a.app
        for cmd in ("sudo xmutil unloadapp", f"sudo xmutil loadapp {app}"):
            rc, out = self.remote_cmd(cmd)
            bad = [ln for ln in out.splitlines() if ERROR.search(ln)]
            if rc != 0 or bad:
                raise Refused(f"`{cmd}` printed {bad[:1] or ['status ' + str(rc)]}: run nothing. "
                              f"{fabric_guard.STOP}")
        self.listapps()

    def listapps(self):
        rc, out = self.remote_cmd("sudo xmutil listapps")
        try:
            active = fabric_guard.parse(out) if rc == 0 else None
        except fabric_guard.NoActiveApp as e:
            raise Refused(str(e))
        if active != self.a.app:
            raise Refused(f"`xmutil listapps` shows {active or 'no app'} active, not {self.a.app}: run nothing. "
                          f"{fabric_guard.STOP}")
        self.say(f"  {self.a.app} is the one active row")

    def smoke(self):
        for name, cmd, all_pass in tests(self.a.app):
            expect = f" --expect {shlex.quote(self.a.expect)}" if self.a.expect and "bringup" in cmd else ""
            t = time.monotonic()
            rc, out = self.remote_cmd(f"cd {shlex.quote(self.a.app)} && {cmd}{expect}", self.a.timeout)
            ok = rc == 0 and (not all_pass or "ALL PASS" in out.splitlines())
            self.results.append((name, ok, time.monotonic() - t))
            self.say(f"[{name}] {'PASS' if ok else 'FAIL'} ({time.monotonic() - t:.0f} s)")
            if not ok:
                raise Failed(f"{name} failed: the run stops here (the app stays loaded; read the output above)")

    def run(self):
        a = self.a
        try:
            self.say(f"[check] {a.app} from {a.out_dir}")
            self.check()
            self.say("[board]")
            self.alive()
            if "install" in a.phases:
                self.say("[install]")
                self.install()
            self.say("[load]" if "load" in a.phases else "[installed]" if "smoke" not in a.phases
                     else "[already loaded?]")
            self.preflight()
            if "load" not in a.phases and "smoke" not in a.phases:
                self.say(f"{a.app} installed, not loaded (no load or smoke phase)")
                return EXIT_OK
            if "load" in a.phases:
                self.load()
            else:
                self.listapps()
            if "smoke" in a.phases:
                self.say("[smoke]")
                self.smoke()
                self.say("ALL PASS: " + ", ".join(n for n, _, _ in self.results))
            return EXIT_OK
        except Refused as e:
            self.say(f"REFUSED: {e}")
            return EXIT_REFUSED
        except Failed as e:
            self.say(f"FAILED: {e}")
            return EXIT_FAILED


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--app", default="protoemu3c", choices=sorted(fabric_guard.APP_BUILD))
    ap.add_argument("--out-dir", default=str(HERE / "out"), help="build.sh's OUT_DIR")
    ap.add_argument("--phases", default=",".join(PHASES), help="comma-separated subset of " + ",".join(PHASES))
    ap.add_argument("--expect", help="bringup_kria.py --expect (default: the installed expected_build.json)")
    ap.add_argument("--timeout", type=float, default=1800, help="seconds per test on the board")
    ap.add_argument("--log", help="also write the output here")
    a = ap.parse_args(argv)
    a.phases = [p for p in a.phases.split(",") if p]
    if not a.phases or set(a.phases) - set(PHASES):
        ap.error(f"--phases: a subset of {','.join(PHASES)}")
    a.kria = os.environ.get("WL_KRIA", "")
    if not a.kria:
        ap.error("set WL_KRIA to the board's ssh address, e.g. WL_KRIA=ubuntu@kria.example")
    return a


def main(argv=None):
    a = parse_args(argv)
    log = open(a.log, "a") if a.log else None

    def say(line):
        print(line, flush=True)
        if log:
            log.write(line + "\n")
            log.flush()
    try:
        say(f"board_smoke.py {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC, phases {','.join(a.phases)}")
        return Smoke(a, Ssh(a.kria), say=say).run()
    finally:
        if log:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
