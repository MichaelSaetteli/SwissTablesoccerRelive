"""Upload staging package (Issue #15).

Splits the operator-facing upload flow into three concerns:

* ``manifest`` — count/bytes verification (no SHA-256 in MVP)
* ``atomic_handoff`` — staging_path -> eingang_<disc>/ETxx/ via os.rename
* ``paths`` — derives staging/final paths from config + table name, enforces
  INV-1 (everything stays on SSD)
"""

from upload_staging.atomic_handoff import (
    HandoffError,
    atomic_release,
)
from upload_staging.manifest import (
    ManifestError,
    verify_manifest,
)
from upload_staging.paths import (
    StagingPathError,
    final_path_for,
    staging_path_for,
    validate_staging_root,
)

__all__ = [
    "HandoffError",
    "ManifestError",
    "StagingPathError",
    "atomic_release",
    "final_path_for",
    "staging_path_for",
    "validate_staging_root",
    "verify_manifest",
]
