Commit: 80cc0b9  Base: 49a2d89  Branch: hw-kit
Item: fabric_guard.py and its use in pe_kria.py, pe_spim.py, pe_lab.py and hw_day.py; runbook recovery step. Incident: on 2026-11-03 `xmutil loadapp protoemu` returned Load Error -1, the next chained command ran eth_tap.py --measure-spim, and the Kria stopped answering. Likely cause, not proved: Spim reads VERSION through /dev/mem at construction, with no working bitstream. Do not touch the Kria; it is off limits.
Claims:
1. No route in fpga/kv260/host/ reaches a fabric address through /dev/mem without passing the guard. Search for every mmap, every /dev/mem and every direct open, including bringup_kria.py and demos. Try to get past it with a bad xmutil output format, a partly applied overlay, an active app of the wrong name, a stale listing, and a failed subprocess.
2. The guard fails closed: any doubt refuses.
3. The guard's checks touch no fabric. It reads xmutil, sysfs and device tree. xmutil's fabric-free behaviour is by design, source unread and unobserved on this board: judge whether that is good enough, and whether a safer check exists.
4. A failed load stops the runbook, and the first step tells the author how to recover the board and to find why the load failed, read-only.
5. 37/37 mutants; 1836 passed; fakes only.
