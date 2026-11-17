"""claimcheck on throwaway git repositories: each failure mode, and the clean case."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from claimcheck import AuditConfig, Manifest, Retraction, aggregate_metrics, audit, verify
from claimcheck.cli import main


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.test", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "x")


def write_manifest(root: Path, name: str = "run", acc: tuple[float, ...] = (0.80, 0.90), **over) -> Path:
    rows = [{"acc": a} for a in acc]
    m = Manifest(
        experiment=name,
        seeds=list(range(len(acc))),
        args={},
        per_seed_metrics=rows,
        aggregate=aggregate_metrics(rows),
        wall_clock_seconds=1.0,
        git_sha="abc",
        git_dirty=False,
    )
    data = m.to_dict() | over
    path = root / "results" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def good_doc(root: Path, table: str = "| acc |\n|---|\n| 0.85 |\n") -> None:
    (root / "RESULTS.md").write_text(
        f"# R\n\n## Run\n\n<!-- manifest: results/run.json -->\n\n{table}", encoding="utf-8"
    )


# ------------------------------------------------------------------------- aggregate
def test_aggregate_uses_only_metrics_every_seed_has() -> None:
    agg = aggregate_metrics([{"a": 1.0, "b": 2.0}, {"a": 3.0}])
    assert set(agg) == {"a"}
    assert agg["a"] == {"mean": 2.0, "std": 1.0, "min": 1.0, "max": 3.0, "n": 2.0}


def test_manifest_round_trip_keeps_unknown_keys(tmp_path: Path) -> None:
    path = write_manifest(tmp_path, extra_field=7)
    m = Manifest.load(path)
    assert m.extra["extra_field"] == 7
    m.save(tmp_path / "again.json")
    assert json.loads((tmp_path / "again.json").read_text())["extra_field"] == 7


def test_loading_a_non_manifest_raises(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(ValueError):
        Manifest.load(tmp_path / "x.json")


# --------------------------------------------------------------------------- verify
def test_a_clean_repository_verifies(repo: Path) -> None:
    write_manifest(repo)
    good_doc(repo)
    commit_all(repo)
    assert verify(Path("results"), [Path("RESULTS.md")], repo, require_cited=True) == []


def test_a_tag_to_a_missing_manifest_is_caught(repo: Path) -> None:
    write_manifest(repo)
    (repo / "RESULTS.md").write_text("## A\n<!-- manifest: results/nope.json -->\n")
    commit_all(repo)
    assert any("missing file" in p for p in verify(Path("results"), [Path("RESULTS.md")], repo))


def test_an_untracked_manifest_is_not_evidence(repo: Path) -> None:
    write_manifest(repo, "tracked")
    commit_all(repo)
    write_manifest(repo, "run")  # on disk, never committed
    good_doc(repo)
    (repo / "RESULTS.md").write_text("## A\n<!-- manifest: results/run.json -->\n")
    problems = verify(Path("results"), [Path("RESULTS.md")], repo)
    assert any("not tracked" in p for p in problems)


def test_a_dirty_tree_manifest_and_a_seed_mismatch_are_caught(repo: Path) -> None:
    write_manifest(repo, git_dirty=True)
    write_manifest(repo, "two", seeds=[0])
    good_doc(repo)
    commit_all(repo)
    problems = verify(Path("results"), [Path("RESULTS.md")], repo)
    assert any("dirty tree" in p for p in problems)
    assert any("2 per-seed records" in p or "seeds but" in p for p in problems)


def test_an_empty_aggregate_backs_nothing(repo: Path) -> None:
    write_manifest(repo, aggregate={})
    good_doc(repo)
    commit_all(repo)
    assert any("empty aggregate" in p for p in verify(Path("results"), [Path("RESULTS.md")], repo))


def test_a_cited_figure_must_exist_and_be_tracked(repo: Path) -> None:
    write_manifest(repo)
    (repo / "figures").mkdir()
    (repo / "figures" / "a.png").write_bytes(b"x")
    (repo / "RESULTS.md").write_text(
        "## A\n<!-- manifest: results/run.json -->\nSee `figures/a.png` and `figures/b.png`.\n"
    )
    (repo / ".gitignore").write_text("figures/a.png\n")
    commit_all(repo)
    problems = verify(Path("results"), [Path("RESULTS.md")], repo)
    assert any("missing figure 'figures/b.png'" in p for p in problems)
    assert any("'figures/a.png' exists but is not tracked" in p for p in problems)


def test_a_section_with_figures_needs_its_own_tag(repo: Path) -> None:
    write_manifest(repo)
    (repo / "RESULTS.md").write_text(
        "## A\n<!-- manifest: results/run.json -->\n\n## B\n**Figures**: none yet\n"
    )
    commit_all(repo)
    assert any("section 'B'" in p for p in verify(Path("results"), [Path("RESULTS.md")], repo))


def test_a_tracked_manifest_cited_nowhere_is_reported_when_required(repo: Path) -> None:
    write_manifest(repo, "run")
    write_manifest(repo, "orphan")
    good_doc(repo)
    commit_all(repo)
    problems = verify(Path("results"), [Path("RESULTS.md")], repo, require_cited=True)
    assert problems == ["results/orphan.json: tracked but cited by no document"]


# ---------------------------------------------------------------------------- audit
def audit_cfg(**kw) -> AuditConfig:
    return AuditConfig(documents=["RESULTS.md"], **kw)


def test_a_table_number_the_manifest_does_not_hold_is_caught(repo: Path) -> None:
    write_manifest(repo)  # accuracy 0.80 and 0.90, mean 0.85
    good_doc(repo, "| acc |\n|---|\n| 0.91 |\n")
    commit_all(repo)
    problems = audit(repo, audit_cfg())
    assert len(problems) == 1 and "0.91" in problems[0]


def test_numbers_that_are_the_manifest_rounded_or_derived_pass(repo: Path) -> None:
    write_manifest(repo)
    good_doc(repo, "| acc | n | share |\n|---|---|---|\n| 0.85 | 12 | 85% |\n| 0.8 | 2024-01-02 | 0.85 / 100 |\n")
    commit_all(repo)
    assert audit(repo, audit_cfg()) == []


def test_a_row_that_declares_no_tracked_manifest_is_exempt(repo: Path) -> None:
    write_manifest(repo)
    good_doc(repo, "| acc | note |\n|---|---|\n| 0.55 | no tracked manifest |\n")
    commit_all(repo)
    assert audit(repo, audit_cfg()) == []


def test_a_retracted_sentence_coming_back_is_caught(repo: Path) -> None:
    (repo / "README.md").write_text("It never groks.\n")
    commit_all(repo)
    cfg = AuditConfig(retractions=[Retraction("README.md", "never groks", "it groks, see run.json", "2026-01-01")])
    problems = audit(repo, cfg)
    assert problems == ["README.md: still says 'never groks' (retracted 2026-01-01: it groks, see run.json)"]
    (repo / "README.md").write_text("It groks.\n")
    assert audit(repo, cfg) == []


def test_the_index_must_list_every_tracked_manifest(repo: Path) -> None:
    write_manifest(repo, "run")
    write_manifest(repo, "other")
    (repo / "INDEX.md").write_text("| `results/run.json` | RESULTS | active |\n| `results/gone.json` | - | - |\n")
    commit_all(repo)
    problems = audit(repo, AuditConfig(index="INDEX.md"))
    assert any("other.json has no row" in p for p in problems)
    assert any("gone.json" in p and "not tracked" in p for p in problems)


# ------------------------------------------------------------------------------- cli
def test_the_cli_reads_the_config_and_exits_nonzero_on_a_problem(repo: Path, capsys) -> None:
    write_manifest(repo)
    good_doc(repo, "| acc |\n|---|\n| 0.77 |\n")
    (repo / "claimcheck.toml").write_text(
        '[verify]\nresults_dir = "results"\ndocuments = ["RESULTS.md"]\n\n[audit]\ndocuments = ["RESULTS.md"]\n'
    )
    commit_all(repo)
    assert main(["verify", "--config", str(repo / "claimcheck.toml"), "--root", str(repo)]) == 0
    assert main(["check", "--config", str(repo / "claimcheck.toml"), "--root", str(repo)]) == 1
    assert "0.77" in capsys.readouterr().out
