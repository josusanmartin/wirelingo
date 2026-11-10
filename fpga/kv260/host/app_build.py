#!/usr/bin/env python3
"""install.sh's check that a bitstream is the build its app name holds:

    app_build.py APP CHECKOUT_JSON BUILD_INFO_JSON

APP must be one of fabric_guard.APP_BUILD, and build_info.json (build.sh's
record) must give that app's core count and clk_div, or it exits non-zero
before anything is copied: a 2-core bitstream must not be installed as
protoemu3c, nor a 3-core one as protoemu. A record from before build.sh
wrote `cores` (2026-11-10) is a 2-core build of unknown clk_div: accepted for
a 2-core app, with a note. A bitstream from another checkout only warns:
bringup_kria.py's build step refuses it on the board.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fabric_guard  # noqa: E402


def check(app, want, built):
    """Lines to print, or SystemExit with the reason."""
    lines = [f"checkout RTL revision {want['rtl_rev'][:12]}" + (" (dirty)" if want["dirty"] else ""),
             f"bitstream RTL revision {built.get('rtl_rev', 'unknown')[:12]}" + (" (dirty)" if built.get("dirty") else "")
             + f", {built.get('cores', '?')} cores, clk_div {built.get('clk_div', '?')}"]
    if app not in fabric_guard.APP_BUILD:
        raise SystemExit(f"{app} is not one of our apps {list(fabric_guard.APP_BUILD)}: the board's tools would refuse it")
    cores, clk_div = fabric_guard.APP_BUILD[app]
    if "cores" not in built:
        if cores != 2:
            raise SystemExit(f"{app} holds a {cores}-core build; build_info.json doesn't say how many cores this one "
                             f"has (built before build.sh recorded it, so 2): build.sh --cores {cores} {clk_div}")
        lines.append(f"note: build_info.json has no core count or clk_div (an older build: 2 cores); "
                     f"{app} should be clk_div {clk_div}")
    elif (built["cores"], built.get("clk_div")) != (cores, clk_div):
        raise SystemExit(f"{app} holds a {cores}-core clk_div {clk_div} build; this one is {built['cores']} cores, "
                         f"clk_div {built.get('clk_div', '?')}: build.sh --cores {cores} {clk_div}, or install it "
                         f"under the app for its build")
    if built.get("rtl_rev") != want["rtl_rev"] or want["dirty"]:
        lines.append("WARNING: the bitstream was not built from this checkout's RTL; "
                     "bringup_kria.py will fail its build step")
    return lines


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    for line in check(sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])):
        print(line)
