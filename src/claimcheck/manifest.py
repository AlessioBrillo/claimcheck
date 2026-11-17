"""Run manifests: a JSON record of one experiment, carrying its own provenance.

A manifest says which code (git SHA, dirty or not), which configuration and which seeds
produced a set of numbers, and gives the per-seed values and their aggregate. It is the
thing a published number is *tied to*. `claimcheck.verify` and `claimcheck.audit` check
the documents against it.

The schema is the one used by `from-gradient-to-transformer`'s `ResultsManifest`; unknown
keys are kept in `extra`, so a manifest written by that project loads unchanged.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORE_FIELDS = (
    "experiment",
    "seeds",
    "args",
    "per_seed_metrics",
    "aggregate",
    "wall_clock_seconds",
    "git_sha",
    "git_dirty",
)


def aggregate_metrics(per_seed: Sequence[dict[str, float]]) -> dict[str, dict[str, float]]:
    """mean / std / min / max / n per metric.

    Only metrics present in *every* seed are aggregated: an optional measurement that one
    seed did not take has no run-level mean, and inventing one would be fabrication.
    """
    if not per_seed:
        raise ValueError("aggregate_metrics requires at least one seed")
    keys = set(per_seed[0])
    for row in per_seed[1:]:
        keys &= set(row)
    out: dict[str, dict[str, float]] = {}
    for key in sorted(keys):
        values = [float(row[key]) for row in per_seed]
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        out[key] = {
            "mean": mean,
            "std": var**0.5,
            "min": min(values),
            "max": max(values),
            "n": float(len(values)),
        }
    return out


def git_provenance(cwd: Path | None = None, ignore_dirs: Sequence[str] = ("results/",)) -> tuple[str, bool]:
    """(short SHA, tree is dirty). `("unknown", True)` when git is unavailable.

    Changes under `ignore_dirs` do not count as dirty: a manifest is written by the very
    run it records, so counting its own output would make every manifest after the first
    in a batch look dirty.
    """
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=cwd, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=cwd, text=True, stderr=subprocess.DEVNULL
        ).splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown", True
    dirty = any(not line.split(maxsplit=1)[-1].startswith(tuple(ignore_dirs)) for line in status)
    return sha, dirty


@dataclass
class Manifest:
    experiment: str
    seeds: list[int]
    args: dict[str, Any]
    per_seed_metrics: list[dict[str, float]]
    aggregate: dict[str, dict[str, float]]
    wall_clock_seconds: float
    git_sha: str
    git_dirty: bool
    python_version: str = field(default_factory=platform.python_version)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_run(
        cls,
        experiment: str,
        seeds: Sequence[int],
        per_seed_metrics: list[dict[str, float]],
        args: dict[str, Any] | None = None,
        wall_clock_seconds: float = 0.0,
        cwd: Path | None = None,
        **extra: Any,
    ) -> "Manifest":
        sha, dirty = git_provenance(cwd)
        return cls(
            experiment=experiment,
            seeds=list(seeds),
            args=dict(args or {}),
            per_seed_metrics=per_seed_metrics,
            aggregate=aggregate_metrics(per_seed_metrics),
            wall_clock_seconds=wall_clock_seconds,
            git_sha=sha,
            git_dirty=dirty,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        data = {k: getattr(self, k) for k in (*CORE_FIELDS, "python_version", "created_at")}
        data.update(self.extra)
        return data

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in CORE_FIELDS if k not in data]
        if missing:
            raise ValueError(f"{path}: not a manifest, missing {missing}")
        known = {*CORE_FIELDS, "python_version", "created_at"}
        return cls(
            **{k: data[k] for k in CORE_FIELDS},
            python_version=data.get("python_version", "unknown"),
            created_at=data.get("created_at", ""),
            extra={k: v for k, v in data.items() if k not in known},
        )
