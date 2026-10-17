"""Raw file storage and dataset metadata records.

Every raw download is stored unchanged next to a JSON metadata record that
documents the source URL, retrieval timestamp, frequency, coverage and known
limitations. The records are aggregated into a single catalogue used by the
site and the research note, so that every number can be traced back to a
source and a retrieval date.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tmdm.config import RAW_DIR


@dataclass
class DatasetMetadata:
    """Provenance record for one raw dataset."""

    dataset_id: str
    title: str
    publisher: str
    source_url: str
    licence: str
    frequency: str
    coverage: str
    units: str
    retrieved_at: str
    files: list[str] = field(default_factory=list)
    sha256: dict[str, str] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise the record as indented JSON."""
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with minute precision."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_dir(dataset_id: str) -> Path:
    """Return (and create) the raw directory for a dataset."""
    d = RAW_DIR / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_metadata(meta: DatasetMetadata) -> Path:
    """Write the metadata record next to the raw files and return its path."""
    d = raw_dir(meta.dataset_id)
    for f in meta.files:
        p = d / f
        if p.exists():
            meta.sha256[f] = sha256_of(p)
    out = d / "metadata.json"
    out.write_text(meta.to_json() + "\n", encoding="utf-8")
    return out


def read_metadata(dataset_id: str) -> DatasetMetadata | None:
    """Load the metadata record for a dataset, if it exists."""
    p = RAW_DIR / dataset_id / "metadata.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return DatasetMetadata(**data)


def rehash_sharded(dataset_id: str) -> DatasetMetadata | None:
    """Bring a sharded download's metadata record up to date with the merged shards on disk.

    The shards of a download run as parallel jobs and the one that writes the record sees the other shards
    only as of its last checkpoint. Once every shard is merged, the record is rewritten with all shard files,
    their current hashes and the latest retrieval time in the done lists.
    """
    meta = read_metadata(dataset_id)
    if meta is None:
        return None
    d = raw_dir(dataset_id)
    shard_files = sorted(p.name for p in d.glob("*shard*") if p.is_file())
    meta.files = sorted(f for f in set(meta.files) | set(shard_files) if (d / f).is_file())
    meta.sha256 = {}
    stamps = []
    for done in d.glob("done*.csv"):
        with done.open(encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
            if "retrieved_at" in header:
                i = header.index("retrieved_at")
                stamps += [line.strip().split(",")[i] for line in fh if line.strip()]
    if stamps:
        meta.retrieved_at = max(stamps)
    write_metadata(meta)
    return meta


def all_metadata() -> list[DatasetMetadata]:
    """Return every metadata record found under the raw data directory."""
    records: list[DatasetMetadata] = []
    if not RAW_DIR.exists():
        return records
    for p in sorted(RAW_DIR.glob("*/metadata.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        records.append(DatasetMetadata(**data))
    return records
