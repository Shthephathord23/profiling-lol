"""The actual work.

Deliberately written with a few distinct hot spots at different depths, so
every shipped profiler has something recognisable to show:

  * ``checksum_records``  -- hashing, dominated by library C code
  * ``normalise_records`` -- string work in pure Python
  * ``score_records``     -- arithmetic in a tight loop
"""

from __future__ import annotations

import hashlib
import math
from typing import Dict, Iterable, List

Record = Dict[str, object]


def normalise_records(records: Iterable[Record]) -> List[Record]:
    """Lower-case and strip the text fields of every record."""
    out: List[Record] = []
    for record in records:
        clean: Record = {}
        for key, value in record.items():
            if isinstance(value, str):
                clean[key] = " ".join(value.strip().lower().split())
            else:
                clean[key] = value
        out.append(clean)
    return out


def score_records(records: Iterable[Record]) -> List[float]:
    """A tight arithmetic loop: the classic line-profiler showcase."""
    scores: List[float] = []
    for record in records:
        weight = float(record.get("weight", 1.0))
        total = 0.0
        for i in range(1, 64):
            total += math.sin(i * weight) / i
        scores.append(total * math.log1p(abs(weight)))
    return scores


def checksum_records(records: Iterable[Record]) -> str:
    """Hash the whole batch; most of the time is spent inside hashlib."""
    digest = hashlib.sha256()
    for record in records:
        for key in sorted(record):
            digest.update(str(key).encode("utf-8"))
            digest.update(b"=")
            digest.update(str(record[key]).encode("utf-8"))
            digest.update(b";")
    return digest.hexdigest()


def process(records: List[Record]) -> Dict[str, object]:
    """One iteration of the workload."""
    normalised = normalise_records(records)
    scores = score_records(normalised)
    return {
        "count": len(normalised),
        "checksum": checksum_records(normalised),
        "mean_score": sum(scores) / len(scores) if scores else 0.0,
    }
