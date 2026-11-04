"""One case of the low-speed per-access table. usage: ls_case.py TREE RESUME_INDEX N [MODULE] [FUNC]
MODULE/FUNC: the scenario to run (default test_usb_ls_restage / restage_race_ls)."""
import sys
tree, ri, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
overhead = int(sys.argv[6]) if len(sys.argv) > 6 else 48
mod = sys.argv[4] if len(sys.argv) > 4 else "test_usb_ls_restage"
func = sys.argv[5] if len(sys.argv) > 5 else "restage_race_ls"
sys.path[:0] = [tree + "/tools", tree + "/test/model"]
L = __import__(mod)
resume = ("after the SETUP", "after its token")[ri]
gen = L.bridges(overhead) if mod == "test_usb_ls_restage" else L.bridges()
make = next(gen)
try:
    outcome, d = getattr(L, func)(make, resume, L.nth_access(n))
except (AssertionError, TimeoutError) as e:
    outcome, d = f"ERROR {type(e).__name__}: {e}", {}
finally:
    for _ in gen:
        pass
print(f"overhead {overhead:5d} {resume:16s} n={n:2d}  {outcome:8s} {d}", flush=True)
