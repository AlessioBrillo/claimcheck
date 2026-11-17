"""claimcheck: every published number tied to a tracked run manifest, checked in CI."""

from .audit import AuditConfig, Retraction, audit
from .manifest import Manifest, aggregate_metrics, git_provenance
from .verify import verify

__all__ = [
    "AuditConfig",
    "Manifest",
    "Retraction",
    "aggregate_metrics",
    "audit",
    "git_provenance",
    "verify",
]
__version__ = "0.1.0"
