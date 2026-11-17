"""`audit`: the prose around the tags must not contradict the manifests.

`verify` proves that evidence exists and is attached. It cannot tell that a table next to
a tag says 0.91 where the manifest says 0.81, or that a sentence the project already
retracted has crept back in. Three checks cover that, none of which parses free prose:

* **numbers**: every number in a Markdown table is found in the manifests cited by the
  section it lives in (as a recorded value, a mean/std/min/max, a rounding of one, or a
  simple quotient of one and another number in the same table);
* **retractions**: a list of sentences known to be false must stay out of their files.
  This is a ratchet: it only ever grows, and each entry carries the date and the reason;
* **index**: every tracked manifest has a row in an index document that says what cites
  it, and every row names a manifest that exists.

The retractions and the document list are configuration (`claimcheck.toml`), because they
are exactly the part that belongs to one project.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .verify import tags_in

DEFAULT_ALLOW_PATTERNS = (
    r"^\d{4}-\d{2}-\d{2}$",  # a date
    r"^\d+$",  # a count
    r"^\d+/\d+$",  # a ratio of counts
    r"^\d+[–-]\d+$",  # a range of counts
)
UNIT_DIVISORS = (3600.0, 60.0, 1000.0, 100.0)
NO_MANIFEST_MARKER = "no tracked manifest"
CELL_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)(?![.\d])\s*(?:[–-]\s*(\d+(?:\.\d+)?))?\s*(%)?")


@dataclass(frozen=True)
class Retraction:
    file: str
    sentence: str
    reason: str
    date: str


@dataclass
class AuditConfig:
    documents: list[str] = field(default_factory=list)
    allow_values: list[float] = field(default_factory=list)
    retractions: list[Retraction] = field(default_factory=list)
    index: str | None = None
    results_dir: str = "results"


# ---------------------------------------------------------------------------- numbers
def manifest_numbers(path: Path) -> set[float]:
    """Every number a manifest can back: per-seed values and aggregate statistics."""
    out: set[float] = set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for row in data.get("per_seed_metrics") or []:
        for v in row.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.add(float(v))
    for stats in (data.get("aggregate") or {}).values():
        if isinstance(stats, dict):
            for key in ("mean", "std", "min", "max"):
                v = stats.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.add(float(v))
    return out


def numbers_in_cell(cell: str) -> list[tuple[float, int]]:
    """(value, decimals) for each number in a table cell; percents are divided by 100."""
    out: list[tuple[float, int]] = []
    for m in CELL_NUMBER_RE.finditer(cell):
        for raw in (m.group(1), m.group(2)):
            if not raw:
                continue
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            value = float(raw)
            if m.group(3):
                value, decimals = value / 100.0, decimals + 2
            out.append((value, decimals))
    return out


def matches(candidate: float, decimals: int, pool: set[float]) -> bool:
    tol = 0.75 * 10.0**-decimals
    return any(
        abs(candidate - v) <= tol or round(v, decimals) == round(candidate, decimals) for v in pool
    )


def derivable(candidate: float, decimals: int, pool: set[float], table: set[float]) -> bool:
    """A quotient of a manifest value and a number the table itself carries
    ('96.6 / 512' is a count over a dictionary size, a wall clock in hours...)."""
    tol = 0.75 * 10.0**-decimals
    dens = {d for d in table if abs(d) > 1e-9} | set(UNIT_DIVISORS)
    return any(abs(candidate - n / d) <= tol for n in pool for d in dens)


def split_row(row: str) -> list[str]:
    ph = "\x00PIPE\x00"
    return [
        c.strip().replace(ph, "|") for c in row.replace("\\|", ph).strip().strip("|").split("|")
    ]


def is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(c) <= set("-: ") for c in cells)


def _check_table(
    rel: str,
    rows: list[str],
    pools: list[set[float]],
    tags: list[str],
    allow: tuple[re.Pattern[str], ...],
    allow_values: frozenset[float],
) -> list[str]:
    if any(NO_MANIFEST_MARKER in r for r in rows):
        return []  # a declared historical table: exempt, and it says so
    table_numbers: set[float] = set()
    for row in rows:
        for cell in split_row(row):
            for v, _ in numbers_in_cell(cell):
                table_numbers.add(v)
    problems = []
    for row in rows:
        for cell in split_row(row):
            if not cell or cell.startswith("<!--"):
                continue
            for value, decimals in numbers_in_cell(cell):
                raw = f"{value:g}"
                if any(p.match(raw) for p in allow) or round(value, 6) in allow_values:
                    continue
                if any(matches(value, decimals, p) for p in pools):
                    continue
                if any(derivable(value, decimals, p, table_numbers) for p in pools):
                    continue
                problems.append(
                    f"{rel}: the number {raw} in a table is not in the manifests this section "
                    f"cites ({', '.join(tags)})"
                )
    return problems


def check_numbers(root: Path, cfg: AuditConfig) -> list[str]:
    allow = tuple(re.compile(p) for p in DEFAULT_ALLOW_PATTERNS)
    allow_values = frozenset(round(v, 6) for v in cfg.allow_values)
    problems: list[str] = []
    for rel in cfg.documents:
        path = root / rel
        if not path.exists() or path.suffix == ".tex":
            continue
        for section in re.split(r"(?=^##\s)", path.read_text(encoding="utf-8"), flags=re.MULTILINE):
            tags = sorted(set(tags_in(section)))
            pools = [manifest_numbers(root / t) for t in tags if (root / t).exists()]
            if not pools:
                continue
            rows: list[str] = []
            for line in section.splitlines() + [""]:
                s = line.strip()
                if s.startswith("|") and s.endswith("|"):
                    if not is_separator(split_row(s)):
                        rows.append(s)
                elif rows:
                    problems += _check_table(rel, rows, pools, tags, allow, allow_values)
                    rows = []
    return problems


# -------------------------------------------------------------------------- retractions
def check_retractions(root: Path, cfg: AuditConfig) -> list[str]:
    problems = []
    for r in cfg.retractions:
        path = root / r.file
        if not path.exists():
            problems.append(f"{r.file}: file not found; the retraction list is stale")
        elif r.sentence in path.read_text(encoding="utf-8"):
            problems.append(f"{r.file}: still says '{r.sentence}' (retracted {r.date}: {r.reason})")
    return problems


# ------------------------------------------------------------------------------- index
def _tracked_manifests(root: Path, results_dir: str) -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", f"{results_dir}/*.json"], cwd=root, capture_output=True, text=True
    ).stdout.split()
    return {Path(p).name for p in out}


def check_index(root: Path, cfg: AuditConfig) -> list[str]:
    if not cfg.index:
        return []
    index = root / cfg.index
    if not index.exists():
        return [f"{cfg.index}: file not found"]
    rows = set(
        re.findall(
            rf"^\|\s*`{re.escape(cfg.results_dir)}/([^`]+\.json)`",
            index.read_text(encoding="utf-8"),
            re.M,
        )
    )
    tracked = _tracked_manifests(root, cfg.results_dir)
    problems = [
        f"{cfg.index}: tracked manifest {cfg.results_dir}/{n} has no row"
        for n in sorted(tracked - rows)
    ]
    problems += [
        f"{cfg.index}: row for {cfg.results_dir}/{n} names a manifest that is not tracked"
        for n in sorted(rows - tracked)
    ]
    return problems


def audit(root: Path, cfg: AuditConfig) -> list[str]:
    return check_retractions(root, cfg) + check_numbers(root, cfg) + check_index(root, cfg)


def summarize(problems: Sequence[str]) -> str:
    if not problems:
        return "audit: the prose and the tables agree with the manifests."
    return f"audit: {len(problems)} problem(s) found:\n" + "\n".join(f"  - {p}" for p in problems)
