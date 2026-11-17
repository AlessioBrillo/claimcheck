"""`verify`: every number a document publishes must be tied to a tracked manifest.

A document marks the manifest behind a section with a tag, `<!-- manifest: path.json -->`
in Markdown or a `% manifest: path.json` line in LaTeX. This module checks the contract:

* every manifest is well formed, its seed count matches its per-seed records and its
  aggregate, and it was not recorded against a dirty tree;
* every tag points at a *tracked* `.json` manifest with a non-empty aggregate (a file that
  exists only on one disk, or an empty test run, backs nothing);
* every figure a document cites exists and is tracked (a clone must see what you saw);
* a Markdown section that shows `**Figures**:` or `**Outputs**:` carries its own tag;
* every manifest a repository tracks is cited somewhere (optional, see `require_cited`).

It checks that evidence exists and is attached. It does not read prose: see `audit` for
tables and for sentences that were retracted.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from .manifest import Manifest

TAG_RE = re.compile(
    r"<!--\s*manifest:\s*(\S+)\s*-->|^[ \t]*%[ \t]*manifest:[ \t]*(\S+)[ \t]*$", re.MULTILINE
)
FIGURE_RE = re.compile(r"`([^`]*figures/[^`]*\.(?:png|svg|pdf))`")
HEADING_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)
INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")


def tags_in(text: str) -> list[str]:
    return [a or b for a, b in TAG_RE.findall(text)]


def git_tracked(path: Path, root: Path) -> bool:
    return (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=root,
            capture_output=True,
        ).returncode
        == 0
    )


def check_manifest_file(path: Path) -> list[str]:
    try:
        m = Manifest.load(path)
    except (ValueError, json.JSONDecodeError) as e:
        return [f"{path}: cannot be loaded as a manifest ({e})"]
    problems = []
    if m.git_dirty:
        problems.append(f"{path}: recorded on a dirty tree; it may match no commit")
    if len(m.seeds) != len(m.per_seed_metrics):
        problems.append(
            f"{path}: {len(m.seeds)} seeds but {len(m.per_seed_metrics)} per-seed records"
        )
    for key, stats in m.aggregate.items():
        if int(stats["n"]) != len(m.seeds):
            problems.append(f"{path}: aggregate['{key}'].n={stats['n']} but {len(m.seeds)} seeds")
    return problems


def check_tags(source: Path, text: str, root: Path) -> list[str]:
    """Every tag in `text` must resolve to a tracked, non-empty `.json` manifest."""
    problems = []
    for rel in sorted(set(tags_in(text))):
        target = root / rel
        if not target.exists():
            problems.append(f"{source}: tag references missing file '{rel}'")
        elif target.suffix != ".json":
            problems.append(f"{source}: tag '{rel}' must be a .json manifest")
        elif not git_tracked(Path(rel), root):
            problems.append(f"{source}: manifest '{rel}' is not tracked by git")
        else:
            try:
                aggregate = json.loads(target.read_text(encoding="utf-8")).get("aggregate")
            except json.JSONDecodeError as e:
                problems.append(f"{source}: manifest '{rel}' is not valid JSON ({e})")
                continue
            if not aggregate:
                problems.append(f"{source}: manifest '{rel}' has an empty aggregate")
    return problems


def check_document(doc: Path, root: Path) -> list[str]:
    """The contract for one Markdown or LaTeX document."""
    if not doc.exists():
        return [f"{doc}: file not found"]
    text = doc.read_text(encoding="utf-8")
    problems = []
    if not tags_in(text):
        problems.append(f"{doc}: no manifest tags; none of its numbers are traceable")
    problems += check_tags(doc, text, root)

    if doc.suffix == ".tex":
        for ref in sorted(set(INCLUDEGRAPHICS_RE.findall(text))):
            base = doc.parent / ref
            options = [base] if base.suffix else [base.with_suffix(s) for s in (".png", ".pdf")]
            fig = next((p for p in options if p.exists()), None)
            if fig is None:
                problems.append(f"{doc}: includes missing figure '{ref}'")
            elif not git_tracked(fig, root):
                problems.append(
                    f"{doc}: figure '{ref}' is not tracked; no clean clone can build it"
                )
        return problems

    for rel in sorted(set(FIGURE_RE.findall(text))):
        fig = root / rel
        if not fig.exists():
            problems.append(f"{doc}: cites missing figure '{rel}'")
        elif not git_tracked(Path(rel), root):
            problems.append(f"{doc}: figure '{rel}' exists but is not tracked")
    for section in re.split(r"(?=^##\s)", text, flags=re.MULTILINE):
        if re.search(r"\*\*(?:Figures|Outputs)\*\*:", section) and not TAG_RE.search(section):
            m = HEADING_RE.match(section)
            problems.append(
                f"{doc}: section '{m.group(1) if m else '(untitled)'}' shows figures or outputs "
                "but has no manifest tag of its own"
            )
    return problems


def verify(
    results_dir: Path,
    documents: Iterable[Path],
    root: Path = Path("."),
    require_cited: bool = False,
) -> list[str]:
    """All problems found; an empty list means the record checks out.

    Only git-tracked manifests are considered: an untracked file is, by construction, not
    evidence anyone else can see.
    """
    problems: list[str] = []
    docs = list(documents)
    manifests = [
        p
        for p in sorted((root / results_dir).glob("*.json"))
        if git_tracked(p.relative_to(root), root)
    ]
    if not manifests:
        problems.append(f"no tracked manifests in {results_dir}/")
    for path in manifests:
        problems += check_manifest_file(path)
    cited: set[str] = set()
    for doc in docs:
        problems += check_document(doc if doc.is_absolute() else root / doc, root)
        if doc.exists() or (root / doc).exists():
            text = (doc if doc.is_absolute() else root / doc).read_text(encoding="utf-8")
            cited |= {Path(t).as_posix() for t in tags_in(text)}
    if require_cited:
        for path in manifests:
            rel = path.relative_to(root).as_posix()
            if rel not in cited:
                problems.append(f"{rel}: tracked but cited by no document")
    return problems


def summarize(problems: Sequence[str]) -> str:
    if not problems:
        return "verify: all manifests and tags check out."
    return f"verify: {len(problems)} problem(s) found:\n" + "\n".join(f"  - {p}" for p in problems)
