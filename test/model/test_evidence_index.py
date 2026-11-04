"""evidence/README.md's table lists every folder under evidence/, once, and
nothing else: each row's link is `[name/](name/README.md)`, and that README
exists. A folder added without a row (or a row left for a removed folder)
fails here. `python3 test/model/test_evidence_index.py` runs it without pytest.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence"
ROW = re.compile(r"^\| \[([^\]/]+)/\]\(([^)]+)\) \|")


def rows(text):
    """(name, link) for each table row of the form | [name/](link) | ..."""
    return [m.groups() for line in text.splitlines() if (m := ROW.match(line))]


def check(evidence):
    listed = rows((evidence / "README.md").read_text())
    names = [n for n, _ in listed]
    folders = sorted(p.name for p in evidence.iterdir() if p.is_dir())
    assert len(names) == len(set(names)), names
    assert sorted(names) == folders, (sorted(names), folders)
    for name, link in listed:
        assert link == f"{name}/README.md", (name, link)
        assert (evidence / link).is_file(), link


def test_every_evidence_folder_has_one_row():
    check(EVIDENCE)


def test_a_folder_without_a_row_is_caught(tmp_path):
    import pytest
    for name in ("a-20261101", "b-20261102"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "README.md").write_text("# x\n")
    table = "| Folder | What | Level |\n|---|---|---|\n| [a-20261101/](a-20261101/README.md) | x | y |\n"
    (tmp_path / "README.md").write_text(table)
    with pytest.raises(AssertionError):
        check(tmp_path)
    (tmp_path / "README.md").write_text(table + "| [b-20261102/](b-20261102/README.md) | x | y |\n")
    check(tmp_path)
    (tmp_path / "README.md").write_text(table + "| [b-20261102/](b-20261102/notes.md) | x | y |\n")
    with pytest.raises(AssertionError):
        check(tmp_path)


if __name__ == "__main__":
    check(EVIDENCE)
    print("evidence/README.md: one row per folder")
