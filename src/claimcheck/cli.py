"""`claimcheck verify | audit | check` driven by `claimcheck.toml`."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from .audit import AuditConfig, Retraction, audit
from .audit import summarize as summarize_audit
from .verify import summarize as summarize_verify
from .verify import verify

DEFAULT_CONFIG = "claimcheck.toml"


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"claimcheck: {path} not found (see the README for the format)")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def audit_config(raw: dict) -> AuditConfig:
    a = raw.get("audit", {})
    return AuditConfig(
        documents=list(a.get("documents", raw.get("verify", {}).get("documents", []))),
        allow_values=[float(v) for v in a.get("allow_values", [])],
        retractions=[Retraction(**r) for r in a.get("retractions", [])],
        index=a.get("index"),
        results_dir=raw.get("verify", {}).get("results_dir", "results"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claimcheck", description=__doc__)
    parser.add_argument("command", choices=["verify", "audit", "check"])
    parser.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    raw = load_config(args.config)
    root = args.root.resolve()
    failed = False
    if args.command in ("verify", "check"):
        v = raw.get("verify", {})
        problems = verify(
            Path(v.get("results_dir", "results")),
            [Path(d) for d in v.get("documents", [])],
            root,
            require_cited=bool(v.get("require_cited", False)),
        )
        print(summarize_verify(problems))
        failed |= bool(problems)
    if args.command in ("audit", "check"):
        problems = audit(root, audit_config(raw))
        print(summarize_audit(problems))
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
