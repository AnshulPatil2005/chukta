"""Append-only provenance log.

One line of JSON per decision, in the shape:

    trigger -> evidence -> rule fired -> gate results -> action -> result -> outcome

JSONL rather than a database, deliberately: it is greppable, diffable, and it
scrolls legibly on camera. Nothing in this module ever updates or deletes a
row. A correction is a new row.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .types import Decision


GENESIS = "0" * 64


class AuditLog:
    """Append-only, and tamper-evident rather than tamper-evident by convention.

    Every row carries `prev` - the digest of the row before it - and `digest`,
    the hash of its own content plus `prev`. Editing any row invalidates every
    digest after it, so "someone quietly changed a decision after the fact"
    becomes detectable instead of unfalsifiable.

    This is a hash chain, not a signature. It proves *internal* consistency: a
    reader can tell the file has not been edited piecewise, but anyone who can
    rewrite the whole file can also recompute the chain. Making that harder
    needs an external anchor - a signature, or publishing the head digest
    somewhere the writer does not control - and that is out of scope here. The
    honest claim is "edits are detectable", not "edits are impossible".
    """

    def __init__(self, path: str | Path, run_id: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._count = 0
        self._head = GENESIS

        # Resume an existing chain rather than restarting it.
        #
        # The first version did not, so appending to a log written by an earlier
        # process produced a row claiming prev=GENESIS in the middle of the
        # file, and `verify` correctly reported the chain as broken. An
        # append-only log that cannot survive a process restart is broken for
        # the one job it exists to do - and this was found by running the live
        # demo twice, not by any test.
        if self.path.exists():
            last = None
            count = 0
            for row in read(self.path):
                last = row
                count += 1
            if last is not None:
                self._count = count
                self._head = last.get("digest", GENESIS)

    @property
    def head(self) -> str:
        """Digest of the most recent row. Publishing this is what would turn
        internal consistency into external tamper-evidence."""
        return self._head

    def _append(self, row: dict) -> None:
        row["run_id"] = self.run_id
        row["seq"] = self._count
        row["prev"] = self._head
        # The digest covers the row without the digest field itself, with keys
        # sorted so it does not depend on dict insertion order.
        body = json.dumps(row, default=_fallback, ensure_ascii=False, sort_keys=True)
        row["digest"] = hashlib.sha256(body.encode("utf-8")).hexdigest()

        self._head = row["digest"]
        self._count += 1
        with io.open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, default=_fallback, ensure_ascii=False) + "\n")

    def write(self, decision: Decision) -> None:
        self._append(decision.to_row())

    def note(self, kind: str, **fields: Any) -> None:
        """Record something that is not a decision - a run boundary, a
        circuit-breaker trip, an operator override."""
        self._append({"kind": kind, **fields})

    def __len__(self) -> int:
        return self._count


def verify(path: str | Path) -> tuple[bool, str | None]:
    """Recompute the chain. Returns (ok, first_broken_description).

    Run this before believing anything an audit file says.
    """
    prev = GENESIS
    for i, row in enumerate(read(path)):
        claimed = row.get("digest")
        if claimed is None:
            return False, f"row {i} (seq {row.get('seq')}) has no digest"
        if row.get("prev") != prev:
            return False, (
                f"row {i} (seq {row.get('seq')}) breaks the chain: prev is "
                f"{row.get('prev', '')[:12]}..., expected {prev[:12]}..."
            )
        body = {k: v for k, v in row.items() if k != "digest"}
        recomputed = hashlib.sha256(
            json.dumps(body, default=_fallback, ensure_ascii=False, sort_keys=True)
            .encode("utf-8")
        ).hexdigest()
        if recomputed != claimed:
            return False, f"row {i} (seq {row.get('seq')}) content does not match its digest"
        prev = claimed
    return True, None


def read(path: str | Path) -> Iterator[dict]:
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _fallback(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return str(obj)
