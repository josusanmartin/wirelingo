"""pytest plugin for the model test suite (never test_swd.py).

    PYTHONPATH=tools:ocaml/test WL_ASM_CORPUS=/tmp/x WL_LOCK_OUT=/tmp/y \
        pytest -p capture_plugin --ignore=test/model/test_swd.py test/model

WL_ASM_CORPUS: record every assemble_all() call, for asm_parity.py --corpus.
WL_LOCK_OUT:   run every Chip in lockstep with the OCaml model (lock_chip.py).
At the end of each test the test's chips are compared on coverage counters too.

SPDX-License-Identifier: Apache-2.0
"""
import json
import os

import pytest

from pe import asm

_assemble_all = asm.assemble_all
corpus = None


def record(text, defines=None):
    if "swd" in text.lower()[:400]:
        raise RuntimeError("SWD source reached the capture plugin")
    corpus.write(json.dumps({"text": text, "defines": {k: v for k, v in (defines or {}).items()}}) + "\n")
    return _assemble_all(text, defines)


def pytest_configure(config):
    global corpus
    if os.environ.get("WL_ASM_CORPUS"):
        corpus = open(f"{os.environ['WL_ASM_CORPUS']}.{os.getpid()}.jsonl", "a")
        asm.assemble_all = record
    if os.environ.get("WL_LOCK_OUT"):
        import lock_chip
        lock_chip.patch()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    yield
    if os.environ.get("WL_LOCK_OUT"):
        import gc
        import lock_chip
        lock_chip.finish_all()
        gc.collect()
        if lock_chip.FAILS:
            fails = lock_chip.FAILS[:]
            lock_chip.FAILS.clear()
            lock_chip.TOTALS.setdefault("failed_tests", []).append(item.nodeid)
            with open(f"{os.environ['WL_LOCK_OUT']}.fail.{os.getpid()}.txt", "a") as f:
                f.write(item.nodeid + "\n" + "\n".join(fails) + "\n")
