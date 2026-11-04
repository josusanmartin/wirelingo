"""compare-dev-replay.txt: usb_replay_demo's dev plans at bc98a97 against
c4003a2 (the code that produced it, run on runner1 with the test venv).

    python compare_dev_replay.py plans-bc98a97.json plans-c4003a2.json > compare-dev-replay.txt
"""
import json
import sys

A = json.load(open(sys.argv[1]))
B = json.load(open(sys.argv[2]))
a = A.get("data", A)
b = B.get("data", B)
print("usb_replay_demo dev plans, bc98a97 against c4003a2 (pe.usbdev.DeviceSoftware on usb_ls_dev / usb_fs_dev)")
for n in ("ls-dev", "fs-dev"):
    sa, sb = a[n]["state"], b[n]["state"]
    print(n, "requests equal", sa["requests"] == sb["requests"], "restaged", sa["restaged"], sb["restaged"],
          "wire packets equal", a[n]["wire"] == b[n]["wire"], len(b[n]["wire"]), "pads equal", a[n]["pads"] == b[n]["pads"])
