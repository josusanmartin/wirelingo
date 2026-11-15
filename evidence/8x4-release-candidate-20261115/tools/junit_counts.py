#!/usr/bin/env python3
"""Count the test cases in each JUnit XML file under the given receipt output
directories: cases, failed (failure or error) and skipped. Used read-only on
the release-candidate receipts (README.md in this folder)."""
import glob
import os
import sys
import xml.etree.ElementTree as ET

for d in sys.argv[1:]:
    for x in sorted(glob.glob(os.path.join(d, "**", "*.xml"), recursive=True)):
        cases = failed = skipped = 0
        for c in ET.parse(x).getroot().iter("testcase"):
            cases += 1
            failed += c.find("failure") is not None or c.find("error") is not None
            skipped += c.find("skipped") is not None
        print(f"{os.path.basename(d.rstrip('/'))}/{os.path.relpath(x, d)}: "
              f"{cases} cases, {failed} failed, {skipped} skipped, {cases - failed - skipped} passed")
