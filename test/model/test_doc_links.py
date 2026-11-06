"""Every relative link in README.md and docs/*.md resolves: the file or folder
exists in the repository, and a `#fragment` names a heading (GitHub's anchor
rules) or an explicit `<a id=...>` / `<a name=...>` in the target. Web links
(http, https, mailto) are not followed. Links inside code spans and fenced
code blocks are not links and are skipped.
`python3 test/model/test_doc_links.py` runs it without pytest.
"""
import pathlib
import re
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[2]
LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REF = re.compile(r"^\s{0,3}\[[^\]^][^\]]*\]:\s+<?(\S+?)>?(?:\s|$)", re.M)   # not a [^footnote]:
HTML = re.compile(r"<(?:img|a)\s[^>]*?(?:src|href)=\"([^\"]+)\"", re.I)
ANCHOR = re.compile(r"<a\s[^>]*?(?:id|name)=\"([^\"]+)\"", re.I)
WEB = ("http://", "https://", "mailto:", "ftp://")


def docs(root=ROOT):
    return [root / "README.md", *sorted((root / "docs").glob("*.md"))]


def strip_code(text):
    """The text with fenced blocks and code spans blanked (line numbers kept)."""
    out, fence = [], None
    for line in text.split("\n"):
        m = re.match(r"\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            out.append("")
            continue
        if m:
            fence = m.group(1)
            out.append("")
            continue
        out.append(re.sub(r"(`+)(?:(?!\1).)+?\1", lambda c: " " * len(c.group(0)), line))
    return "\n".join(out)


def slug(heading):
    """GitHub's anchor for a heading's text."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)      # a link's text
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path):
    """The fragments a link to this markdown file may name."""
    found, seen = set(), {}
    text = path.read_text(errors="replace")
    for line in strip_code(text).split("\n"):
        m = re.match(r"\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            s = slug(m.group(1).replace("`", ""))
            n = seen.get(s, 0)
            seen[s] = n + 1
            found.add(s if n == 0 else f"{s}-{n}")
    # headings with code spans: strip_code blanked them, so read those raw
    for m in re.finditer(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", text, re.M):
        found.add(slug(m.group(1).replace("`", "")))
    found |= set(ANCHOR.findall(text))
    return found


def targets(text):
    """(line, target) of each link in the text."""
    clean = strip_code(text)
    out = []
    for pattern in (LINK, REF, HTML):
        for m in pattern.finditer(clean):
            out.append((clean.count("\n", 0, m.start()) + 1, m.group(1)))
    return out


def broken(path, root=ROOT):
    """'file:line: target (why)' for each link in the file that does not resolve."""
    bad = []
    for line, target in targets(path.read_text(errors="replace")):
        if target.startswith(WEB) or target.startswith("//"):
            continue
        where, _, frag = target.partition("#")
        dest = (path.parent / urllib.parse.unquote(where)).resolve() if where else path
        name = f"{path.relative_to(root)}:{line}: {target}"
        try:
            dest.relative_to(root)
        except ValueError:
            bad.append(f"{name} (outside the repository)")
            continue
        if not dest.exists():
            bad.append(f"{name} (no such file)")
        elif frag and dest.suffix == ".md" and urllib.parse.unquote(frag).lower() not in anchors(dest):
            bad.append(f"{name} (no such anchor)")
    return bad


def test_relative_links_resolve():
    bad = [b for p in docs() for b in broken(p)]
    assert not bad, "broken links:\n" + "\n".join(bad)


def test_a_broken_link_is_caught(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("# A title\n\n## Second `code` part\n\ntext\n")
    good = ("[a](docs/a.md) [b](docs/a.md#a-title) [c](docs/a.md#second-code-part)\n"
            "`[x](missing.md)`\n```\n[y](missing.md)\n```\n[w](https://example.com/x)\n")
    (tmp_path / "README.md").write_text(good)
    assert broken(tmp_path / "README.md", tmp_path) == []
    for link in ("[m](docs/missing.md)", "[n](docs/a.md#no-such-part)", "![i](docs/pic.png)"):
        (tmp_path / "README.md").write_text(good + link + "\n")
        assert len(broken(tmp_path / "README.md", tmp_path)) == 1, link


if __name__ == "__main__":
    bad = [b for p in docs() for b in broken(p)]
    print("\n".join(bad) if bad else f"{len(docs())} files: every relative link resolves")
    raise SystemExit(1 if bad else 0)
