# claimcheck

**Tie every number you publish to a run manifest you can inspect, and fail CI when a document disagrees with its evidence.**

Research repositories drift. A README keeps saying "reaches 99%" after the run behind it was re-done at 91%. A table is copied from a notebook that no longer exists. A result is quoted from a file that lives only on one laptop. Nothing fails, because nothing is checking.

`claimcheck` is a small, dependency-free checker for that. It does three things:

| Command | What it enforces |
|---|---|
| `claimcheck verify` | Every manifest is well formed and was recorded on a clean tree. Every tag in a document points at a **git-tracked** manifest with a non-empty aggregate. Every cited figure exists and is tracked. A section that shows figures carries its own tag. |
| `claimcheck audit` | Every number in a Markdown table is found in the manifests that section cites (a recorded value, a mean/std/min/max, a rounding of one, or a simple quotient). A list of **retracted sentences** stays out of their files. An index lists every tracked manifest. |
| `claimcheck check` | Both. |

It checks that evidence exists and is attached, and that tables agree with it. It does **not** read free prose: a number inside a sentence is checked only if you have retracted that sentence. That is a real limit; the retraction list is how a project turns "we found this was wrong once" into "it cannot come back".

## A manifest

One JSON file per run, written when the run finishes:

```python
from claimcheck import Manifest

rows = [run(seed) for seed in (0, 1, 2)]          # [{"val_acc": 0.9998}, ...]
Manifest.from_run("grokking", seeds=[0, 1, 2], per_seed_metrics=rows,
                  args={"lr": 1e-3}, device="cuda").save(Path("results/grokking.json"))
```

It records the seeds, the arguments, the per-seed metrics, their aggregate, the git SHA and whether the tree was dirty. Extra keyword arguments are kept as additional fields.

## A tagged document

```markdown
## Grokking

<!-- manifest: results/grokking.json -->

| metric | value |
|---|---|
| validation accuracy | 0.9999 ± 0.0002 |
```

In LaTeX, put `% manifest: results/grokking.json` on a line of its own.

## Configure and run

```bash
pip install git+https://github.com/AlessioBrillo/claimcheck
cp claimcheck.toml.example claimcheck.toml     # then edit
claimcheck check
```

Exit code 0 means the record checks out, 1 means it does not. Put `claimcheck check` in your CI gate.

## Why not just ...

- **A notebook that prints the table?** Notebooks get edited; a manifest is a committed record with the SHA that produced it.
- **DVC / MLflow / W&B?** They track runs. They do not read your README and tell you it is wrong. `claimcheck` is the other half, and works with any of them: point `results_dir` at wherever you export manifests.

## Origin

Extracted from [from-gradient-to-transformer](https://github.com/AlessioBrillo/from-gradient-to-transformer), where it has caught, among other things, a negative result that the project's own metric had manufactured, a published verdict the committed code could not reproduce, and a random seed that did not pin a model's initial weights. That repository's `src/results.py` and `scripts/docs_audit.py` are the same mechanism with project-specific lists; its manifests load here unchanged (`claimcheck check` passes on its real record).

Python 3.11 or newer. MIT licensed.
