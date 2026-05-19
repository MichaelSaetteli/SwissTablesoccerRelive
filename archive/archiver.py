"""rsync-style file archiver with SHA-256 verification.

We do not actually shell out to rsync - a few hundred MP4s with stable
filenames are well within Python's reach, and staying pure-Python keeps
the code testable without binary dependencies. The flow is:

  1. ``build_archive_plan`` enumerates every file under the source dirs
     and prepares the target path under ``<archive_root>/<tournament>/``.
  2. ``execute_archive`` copies each file (preserving mtime), hashes
     both sides, and only marks success when the SHA-256 sums match.
  3. Optionally ``execute_archive(delete_source=True)`` removes the
     SSD files after the verify step succeeds for *every* file. If even
     one hash mismatches, NOTHING is deleted - the operator gets a clear
     failure report.

``archiv_meta.json`` is written into the archive directory with the
final SHA list, YouTube links (passed in from the caller), and the
discipline-by-discipline byte sums.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from pipeline.status_file import now_iso

sys.stdout.reconfigure(encoding="utf-8")


class ArchiveError(RuntimeError):
    """Raised when archive plan is invalid or verification fails."""


# ---------------------------------------------------------------------------
# Plan + result data classes
# ---------------------------------------------------------------------------

@dataclass
class ArchiveFileEntry:
    """One file that the plan wants to archive."""
    source: str                  # absolute path on SSD
    target: str                  # absolute path on HDD
    size_bytes: int              # source size at plan time
    sha256: Optional[str] = None # filled in by execute_archive


@dataclass
class ArchivePlan:
    tournament_name: str
    tournament_id: Optional[int]
    archive_root: str            # /volume2/HDD12TB/archiv/<slug>/
    discipline_paths: Dict[str, Dict[str, str]] = field(default_factory=dict)
    files: List[ArchiveFileEntry] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def total_files(self) -> int:
        return len(self.files)


@dataclass
class ArchiveResult:
    plan: ArchivePlan
    started_at: str
    finished_at: str
    duration_s: float
    state: str                   # 'done' | 'error'
    bytes_copied: int
    files_verified: int
    files_failed: int
    error: Optional[str] = None
    meta_path: Optional[str] = None
    source_deleted: bool = False


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256_of(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streaming SHA-256 so 50 GB files do not blow up memory."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Compact slug from a tournament name - never crashes on weird input."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "unnamed"


def build_archive_plan(
    *,
    tournament_name: str,
    archive_root: Path,
    sources: Dict[str, Dict[str, Path]],
    tournament_id: Optional[int] = None,
    date_prefix: Optional[str] = None,
) -> ArchivePlan:
    """Walk *sources* and build the per-file copy plan.

    *sources* is a 2-level dict::

        {
          "Doppel": {"eingang": Path(...), "output": Path(...)},
          "Einzel": {"eingang": Path(...), "output": Path(...)},
        }

    The archive layout under ``archive_root`` mirrors this::

        archive_root/
          YYYY-MM-<slug>/
            eingang_doppel/...
            output_doppel/...
            eingang_einzel/...
            output_einzel/...
    """
    archive_root = Path(archive_root)
    if not archive_root.is_dir():
        # Caller must mkdir the root - we should not create directories
        # on the HDD speculatively.
        raise ArchiveError(f"Archive root does not exist: {archive_root}")

    prefix = date_prefix or time.strftime("%Y-%m", time.localtime())
    folder_name = f"{prefix}-{_slug(tournament_name)}"
    archive_dir = archive_root / folder_name

    files: List[ArchiveFileEntry] = []
    discipline_paths: Dict[str, Dict[str, str]] = {}

    for discipline, paths in sources.items():
        sub: Dict[str, str] = {}
        for role, src_dir in paths.items():
            src_dir = Path(src_dir)
            if not src_dir.is_dir():
                continue
            target_sub = f"{role}_{discipline.lower()}"
            target_dir = archive_dir / target_sub
            sub[role] = str(target_dir)
            for entry in sorted(src_dir.rglob("*")):
                if entry.is_file():
                    rel = entry.relative_to(src_dir)
                    files.append(ArchiveFileEntry(
                        source=str(entry),
                        target=str(target_dir / rel),
                        size_bytes=entry.stat().st_size,
                    ))
        discipline_paths[discipline] = sub

    return ArchivePlan(
        tournament_name=tournament_name,
        tournament_id=tournament_id,
        archive_root=str(archive_dir),
        discipline_paths=discipline_paths,
        files=files,
    )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, int, int], None]
# (files_done, files_total, bytes_done, bytes_total)


def execute_archive(
    plan: ArchivePlan,
    *,
    delete_source: bool = False,
    youtube_links: Optional[Dict[str, List[str]]] = None,
    progress: Optional[ProgressCallback] = None,
) -> ArchiveResult:
    """Copy every planned file to its target, verify SHA-256, write meta JSON.

    Order of operations:
      1. Copy file (shutil.copy2 - preserves mtime)
      2. Hash source + target, compare
      3. Bookkeep bytes_copied / files_verified
      4. If even one mismatch -> abort without deleting anything
      5. After all files OK -> write archiv_meta.json
      6. Only if delete_source=True AND all OK -> delete source files
         then prune now-empty source dirs
    """
    started_at = now_iso()
    t0 = time.monotonic()
    archive_dir = Path(plan.archive_root)
    archive_dir.mkdir(parents=True, exist_ok=True)

    bytes_copied = 0
    files_verified = 0
    files_failed = 0
    mismatches: List[str] = []

    for idx, entry in enumerate(plan.files, start=1):
        src = Path(entry.source)
        dst = Path(entry.target)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            src_hash = sha256_of(src)
            dst_hash = sha256_of(dst)
            entry.sha256 = src_hash
            if src_hash != dst_hash:
                files_failed += 1
                mismatches.append(entry.target)
            else:
                files_verified += 1
                bytes_copied += entry.size_bytes
        except OSError as exc:
            files_failed += 1
            mismatches.append(f"{entry.target} ({exc.strerror})")

        if progress is not None:
            try:
                progress(idx, plan.total_files,
                         bytes_copied, plan.total_bytes)
            except Exception:  # pragma: no cover - never break archive on cb
                pass

    finished_at = now_iso()
    duration_s = time.monotonic() - t0

    if files_failed > 0:
        return ArchiveResult(
            plan=plan,
            started_at=started_at,
            finished_at=finished_at,
            duration_s=duration_s,
            state="error",
            bytes_copied=bytes_copied,
            files_verified=files_verified,
            files_failed=files_failed,
            error=f"{files_failed} file(s) failed verification: "
                  f"{mismatches[:5]}",
        )

    # All files verified - write the meta JSON.
    meta = {
        "tournament_name": plan.tournament_name,
        "tournament_id": plan.tournament_id,
        "archiviert_am": finished_at,
        "groesse_gb": round(bytes_copied / 1024 ** 3, 3),
        "youtube_links": youtube_links or {},
        "checksummen_ok": True,
        "files_verified": files_verified,
        "files_total": plan.total_files,
        "discipline_paths": plan.discipline_paths,
    }
    meta_path = archive_dir / "archiv_meta.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    source_deleted = False
    if delete_source:
        # Delete the original files (each was verified above).
        for entry in plan.files:
            try:
                Path(entry.source).unlink()
            except OSError:
                pass
        # Prune now-empty parent directories, leaf-first.
        candidate_dirs = {Path(entry.source).parent for entry in plan.files}
        for d in sorted(candidate_dirs, key=lambda p: -len(p.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
        source_deleted = True
        meta["ssd_freigegeben_am"] = now_iso()
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return ArchiveResult(
        plan=plan,
        started_at=started_at,
        finished_at=finished_at,
        duration_s=duration_s,
        state="done",
        bytes_copied=bytes_copied,
        files_verified=files_verified,
        files_failed=files_failed,
        meta_path=str(meta_path),
        source_deleted=source_deleted,
    )
