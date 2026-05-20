"""Tournament-Archivierung mit Checksumme.

Nach erfolgreichem YouTube-Upload werden alle Roh- und Output-Daten von
der SSD auf eine HDD verschoben. Der Flow ist **explizit manuell** (kein
Auto-Trigger), und jede Datei wird per SHA-256 auf der HDD gegen die
Quelle auf der SSD verifiziert. Erst nach erfolgreicher Verifikation
werden die SSD-Inhalte geleert.
"""

from .archiver import (
    ArchiveError,
    ArchivePlan,
    ArchiveResult,
    build_archive_plan,
    execute_archive,
    sha256_of,
)
from .tiering import (
    STAGEABLE_ROLES,
    SweepResult,
    staged_age_epoch,
    stage_to_hdd,
    sweep_staging,
)

__all__ = [
    "ArchiveError", "ArchivePlan", "ArchiveResult",
    "build_archive_plan", "execute_archive", "sha256_of",
    "STAGEABLE_ROLES", "SweepResult", "staged_age_epoch",
    "stage_to_hdd", "sweep_staging",
]
