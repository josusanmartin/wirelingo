"""Board figures measured with usb_fs_dev / usb_ls_dev before the got_in guard
(branch usb-guard, 2026-11-04) must say so wherever a tracked doc quotes them
(verifier 044): every section (heading to heading) of a tracked .md outside
evidence/ that quotes one of FIGURES also says "before the (got_in) guard".
The figures are the board runs' distinctive numbers; a new copy of one
without the label fails here."""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIGURES = ("17.6 kB/s", "350 of 350", "5 of 116", "every pad edge equal to the model")
LABEL = re.compile(r"before\s+the\s+(got_in\s+)?guard", re.I)


def tracked_docs():
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / f for f in out.split() if not f.startswith("evidence/")]


def sections(text):
    """(first line number, text) of each part between headings."""
    lines = text.split("\n")
    starts = [0] + [i for i, line in enumerate(lines) if re.match(r"#{1,6} ", line)] + [len(lines)]
    return [(a + 1, "\n".join(lines[a:b])) for a, b in zip(starts, starts[1:]) if b > a]


def test_pre_guard_board_figures_are_labelled():
    missing = []
    for path in tracked_docs():
        for line, part in sections(path.read_text(errors="replace")):
            if any(f in part for f in FIGURES) and not LABEL.search(part):
                missing.append(f"{path.relative_to(ROOT)}:{line}")
    assert not missing, f"pre-guard board figures without the label: {missing}"
