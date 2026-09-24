"""Data connector boundary: implement iter_documents, or POST normalized records.

Connectors own credentials and checkpointing. Retrieval never queries arbitrary URLs,
executes SQL supplied by a caller, or follows instructions found in documents.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .config import Settings
from .hub import Hub
from .hub_models import DocumentInput, IngestRequest
from .parsing import SUPPORTED, parse_bytes


class DataAdapter(Protocol):
    def iter_documents(self) -> Iterable[DocumentInput]: ...


def filename_date(name: str) -> str:
    """Use an unambiguous, explicit date in a filename; never filesystem timestamps."""
    dates = set()
    for year, month, day in re.findall(r"(?<!\d)(20\d{2})[-_](\d{2})[-_](\d{2})(?!\d)", name):
        try:
            dates.add(datetime(int(year), int(month), int(day), tzinfo=UTC))
        except ValueError:
            pass
    months = {
        name.lower(): number
        for number, name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
        )
    }
    for day, month, year in re.findall(
        r"(?<!\d)(\d{1,2})[-_ ]([A-Za-z]{3})[-_ ](20\d{2})(?!\d)", name
    ):
        try:
            dates.add(datetime(int(year), months[month.lower()], int(day), tzinfo=UTC))
        except (ValueError, KeyError):
            pass
    return next(iter(dates)).isoformat() if len(dates) == 1 else ""


class LocalFilesAdapter:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.errors: list[dict] = []

    def iter_documents(self):
        if not self.path.exists():
            raise ValueError("input_path_not_found")
        settings = Settings()
        root = self.path if self.path.is_dir() else self.path.parent
        files = sorted(self.path.rglob("*")) if self.path.is_dir() else [self.path]
        for path in files:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            # Never traverse a linked file outside the explicitly selected directory.
            if not path.resolve().is_relative_to(root):
                self.errors.append({"file": path.name, "error": "outside_import_root"})
                continue
            try:
                if path.stat().st_size > settings.max_file_mb * 1024 * 1024:
                    raise ValueError("file_size_limit")
                documents = parse_bytes(path.name, path.read_bytes(), settings)
                for index, doc in enumerate(documents):
                    yield DocumentInput(
                        external_id=f"{path.relative_to(root).as_posix()}#{index}",
                        title=doc.title,
                        body=doc.body,
                        published_at=doc.published_at or filename_date(path.name),
                        url=doc.url,
                        pages=doc.pages,
                        metadata={
                            "original_source": doc.source,
                            "file_name": path.name,
                            "parser_warnings": ";".join(doc.warnings)[:1000],
                            "date_source": "document"
                            if doc.published_at
                            else (
                                "filename_date_utc_midnight"
                                if filename_date(path.name)
                                else "unknown"
                            ),
                        },
                    )
            except (ValueError, OSError, LookupError):
                # Detailed validation messages can include private input; do not log them.
                self.errors.append({"file": path.name, "error": "parse_failed_check_format_or_ocr"})


def sync_adapter(hub: Hub, source_id: str, adapter: DataAdapter):
    """Upsert adapter records; absent records are NOT silently deleted."""
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for document in adapter.iter_documents():
        result = hub.ingest(IngestRequest(source_id=source_id, documents=[document]))
        for key in counts:
            counts[key] += result[key]
    return counts
