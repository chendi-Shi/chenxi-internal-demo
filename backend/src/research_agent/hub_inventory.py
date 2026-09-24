"""Read-only source inventory. Partial downloads never count as complete archives."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inventory(root: Path):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("source_root_unavailable")
    errors, entries = [], []
    for directory, dirs, files in os.walk(root, onerror=lambda _: errors.append("scan_incomplete")):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in sorted(files):
            path = Path(directory) / name
            entry = {"file": path.relative_to(root).as_posix(), "state": "other"}
            try:
                if not path.resolve().is_relative_to(root):
                    raise ValueError("linked_file_outside_root")
                before = path.stat()
                entry.update(bytes=before.st_size, sha256=sha256(path))
                if name.lower().endswith((".qkdownloading", ".part", ".crdownload", ".download")):
                    entry["state"] = "incomplete_download"
                elif path.suffix.lower() == ".zip":
                    with ZipFile(path) as archive:
                        members = archive.infolist()
                        # Bound decompression work even for malicious zip metadata.
                        if (
                            len(members) > 10000
                            or sum(m.file_size for m in members) > 1_000_000_000
                        ):
                            raise ValueError("archive_validation_limit")
                        if archive.testzip() is not None:
                            raise ValueError("archive_crc_failed")
                        entry.update(
                            state="zip_crc_verified",
                            pdf_members=sum(
                                m.filename.lower().endswith(".pdf")
                                for m in members
                                if not m.is_dir()
                            ),
                        )
                elif path.suffix.lower() == ".rar":
                    entry["state"] = "rar_requires_extraction_verification"
                elif path.suffix.lower() == ".pdf":
                    entry["state"] = "pdf_present"
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    entry["state"] = "changing_file_retry"
            except (OSError, ValueError, BadZipFile, RuntimeError, NotImplementedError):
                entry["state"] = "unreadable_or_invalid"
            entries.append(entry)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "files": entries,
        "scan_errors": errors,
        "incomplete_downloads": sum(e["state"] == "incomplete_download" for e in entries),
        "conclusion": "inventory_only_not_proof_of_full_corpus",
    }
