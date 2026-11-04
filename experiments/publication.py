#!/usr/bin/env python3
"""Publication tools: checks before anything leaves this computer, and the Zenodo packages.

  .venv/bin/python -m experiments.publication check      # secrets, client data, third-party URLs, placeholders
  .venv/bin/python -m experiments.publication package    # publication/packages/*.zip + SHA256SUMS.txt

Nothing here publishes anything: repository, Zenodo records and releases are
created by the author (publication/GUIDA_PUBBLICAZIONE_IT.md).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "publication" / "packages"
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{20,}|DFS_MCP_TOKEN=\S+|OPENAI_API_KEY=\S+|Bearer [A-Za-z0-9._-]{20,}")
# Names that must never appear (client domains and the like) are read from an
# untracked file, so that the check itself publishes nothing: one term per line.
PRIVATE = ROOT / ".private" / "never_publish.txt"
URL = re.compile(r"https?://[^\s\"'<>)]+")
OWN = ("alessiopomaro.it", "example.test", "example.com", "example-org", "openalex.org", "doi.org", "orcid.org",
       "github.com", "pypi.org", "creativecommons.org", "apache.org", "zenodo.org", "dataforseo.com",
       "tavily.com", "modelcontextprotocol.io", "proceedings.mlr.press", "arxiv.org", "aclanthology.org",
       "pypi.example.test", "mcp.example.com", "www.anthropic.com", "raw.githubusercontent.com",
       "claude.com")


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.splitlines()]


def private_terms() -> list[str]:
    if not PRIVATE.exists():
        return []
    return [t.strip().lower() for t in PRIVATE.read_text(encoding="utf-8").splitlines()
            if t.strip() and not t.startswith("#")]


def check() -> int:
    problems = []
    terms = private_terms()
    if not terms:
        print(f"warning: {PRIVATE.relative_to(ROOT)} not found: the check for private names is skipped")
    files = tracked() + sorted((ROOT / "dataset").rglob("*.json*"))
    for f in files:
        if not f.is_file() or f.suffix in (".pdf", ".png", ".zip"):
            continue
        rel = f.relative_to(ROOT)
        if rel.name == ".env" or rel.parts[0] in ("traces", "out", "legal"):
            problems.append(f"{rel}: must never be published")
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if SECRET.search(text) and rel.name != "publication.py":    # this file holds the pattern itself
            problems.append(f"{rel}: looks like a secret")
        low = text.lower()
        if any(t in low for t in terms):
            problems.append(f"{rel}: a name from {PRIVATE.relative_to(ROOT)}")
        # Derived records and results must carry no third-party URL.
        if rel.parts[0] in ("results",) or rel.parts[:2] == ("dataset", "derived"):
            for u in URL.findall(text):
                if not any(o in u for o in OWN):
                    problems.append(f"{rel}: third-party URL {u[:60]}")
                    break
    # What will actually be uploaded: the text inside the packages and the PDF.
    for z in sorted(PKG.rglob("*.zip")) if PKG.exists() else []:
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if name.endswith((".pdf", ".png", ".zip")):
                    continue
                text = zf.read(name).decode("utf-8", errors="replace")
                if any(t in text.lower() for t in terms):
                    problems.append(f"{z.relative_to(ROOT)}:{name}: a name from {PRIVATE.relative_to(ROOT)}")
                if SECRET.search(text) and not name.endswith("publication.py"):     # case matters for keys
                    problems.append(f"{z.relative_to(ROOT)}:{name}: looks like a secret")
    pdfs = [ROOT / "paper" / "main.pdf"] + (sorted(PKG.rglob("*.pdf")) if PKG.exists() else [])
    try:
        import pymupdf
        for pdf in pdfs:
            if pdf.exists():
                low = "".join(page.get_text() for page in pymupdf.open(pdf)).lower()
                if any(t in low for t in terms):
                    problems.append(f"{pdf.relative_to(ROOT)}: a name from {PRIVATE.relative_to(ROOT)}")
    except ImportError:
        print("warning: pymupdf not installed: PDF text not checked")
    for f in (ROOT / "publication").glob("*.md"):
        if re.search(r"\bTODO\b|XXXX", f.read_text(encoding="utf-8")):
            problems.append(f"{f.relative_to(ROOT)}: placeholder left")
    print("\n".join(problems) if problems else "check: nothing to fix")
    return 1 if problems else 0


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def zipdir(out: Path, base: Path, paths: list[Path]) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, p.relative_to(base))


def package() -> None:
    if not private_terms():
        raise SystemExit(f"{PRIVATE.relative_to(ROOT)} is missing: packages are never built without the private-name check")
    if check():
        sys.exit("fix the problems above before packaging")
    if PKG.exists():
        shutil.rmtree(PKG)
    (PKG / "1_paper").mkdir(parents=True)
    (PKG / "2_software_and_data").mkdir(parents=True)
    shutil.copy2(ROOT / "paper" / "main.pdf", PKG / "1_paper" / "tracetrim_paper.pdf")
    paper_files = [p for p in (ROOT / "paper").rglob("*") if p.is_file() and p.suffix in (".tex", ".bib")]
    zipdir(PKG / "1_paper" / "paper_sources.zip", ROOT, paper_files)
    # The software release is exactly what git tracks at HEAD: nothing local slips in.
    subprocess.run(["git", "archive", "--format=zip", "--prefix=tracetrim/", "-o",
                    str(PKG / "2_software_and_data" / "tracetrim_software.zip"), "HEAD"], cwd=ROOT, check=True)
    zipdir(PKG / "2_software_and_data" / "tracetrim_dataset.zip", ROOT,
           sorted(p for p in (ROOT / "dataset").rglob("*") if p.is_file()))
    sums = [f"{sha256(p)}  {p.relative_to(PKG)}" for p in sorted(PKG.rglob("*")) if p.is_file()]
    (PKG / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n")
    print("\n".join(sums))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit(check() if cmd == "check" else package())
