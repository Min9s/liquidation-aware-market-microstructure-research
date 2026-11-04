"""SQLite journal of finalized SYNTHETIC replay orders, not exchange messages.

Frozen inputs/code define expected events. Recovery verifies the committed prefix
and rebuilds cash/positions from fills. It does not resume books or pending orders.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .execution import _public, replay_execution
from .execution_contracts import KIND, identity
from .execution_ledger import rebuild_account

SCHEMA = "synthetic-order-journal-v1"
MAX_JSON_BYTES = 32 * 1024**2


def _dump(value):
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Journal requires JSON-serializable finite facts") from error
    if len(text.encode()) > MAX_JSON_BYTES:
        raise ValueError("Journal JSON record exceeds 32 MiB")
    return text


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _definition_sha():
    names = ("execution.py", "execution_contracts.py", "execution_risk.py",
             "execution_ledger.py", "execution_journal.py")
    return _sha(_dump({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                       for name in names}))


def _connect(path):
    # mode=rw never creates a missing database during recovery.
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


class ExecutionJournal:
    """One frozen synthetic run per database; event and account commit atomically.

    Each IOC order has one finalized event, optionally carrying one aggregate
    simulated fill. Event ID equals order ID. No partial exchange-report protocol.
    """

    def __init__(self, connection, bundle):
        self._connection = connection
        self._bundle = bundle
        self._metadata = dict(schema=SCHEMA, bundle=bundle, bundle_sha256=_sha(_dump(bundle)),
                              definition_sha256=_definition_sha())
        result = replay_execution(bundle)
        fills = {fill["order_id"]: fill for fill in result["fills"]}
        self._events = [dict(event_id=row["order_id"], source_kind=KIND,
                             order=row, fill=fills.get(row["order_id"])) for row in result["orders"]]

    @classmethod
    def create(cls, path, bundle):
        """Validate before creating a new file; existing paths are never replaced."""
        frozen = json.loads(_dump(bundle))
        replay_execution(frozen)
        metadata = dict(schema=SCHEMA, bundle=frozen, bundle_sha256=_sha(_dump(frozen)),
                        definition_sha256=_definition_sha())
        path = Path(path).absolute()
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        connection = None
        try:
            connection = _connect(path)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE events(sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE account(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            connection.execute("INSERT INTO metadata VALUES(1, ?)", (_dump(metadata),))
            store = cls(connection, frozen)
            connection.execute("INSERT INTO account VALUES(1, ?)", (_dump(store._account([])),))
            connection.commit()
            return store
        except BaseException:
            if connection is not None:
                connection.close()
            # A failed/partial initialization remains visible; never erase evidence.
            raise

    @classmethod
    def open(cls, path):
        path = Path(path).absolute()
        if path.is_symlink():
            raise ValueError("Journal must be an ordinary database path")
        connection = _connect(path)
        try:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT payload FROM metadata WHERE id=1").fetchall()
            if len(rows) != 1:
                raise ValueError("Missing journal metadata")
            metadata = json.loads(rows[0][0])
            if (set(metadata) != {"schema", "bundle", "bundle_sha256", "definition_sha256"}
                    or metadata["schema"] != SCHEMA
                    or metadata["bundle_sha256"] != _sha(_dump(metadata["bundle"]))
                    or metadata["definition_sha256"] != _definition_sha()):
                raise ValueError("Journal inputs or code definition changed")
            store = cls(connection, metadata["bundle"])
            store._verify()
            connection.commit()
            return store
        except BaseException:
            connection.close()
            raise

    def events(self):
        """Expected finalized events from the frozen offline replay, in order."""
        return deepcopy(self._events)

    def _account(self, events):
        policy = self._bundle["policy"]
        return _public(rebuild_account([e["fill"] for e in events if e["fill"] is not None],
                                       initial_cash=policy["initial_cash"], fee_bps=policy["fee_bps"]))

    def _verify(self):
        metadata = self._connection.execute("SELECT payload FROM metadata WHERE id=1").fetchall()
        if len(metadata) != 1 or _dump(json.loads(metadata[0][0])) != _dump(self._metadata):
            raise ValueError("Frozen journal metadata changed")
        records = self._connection.execute(
            "SELECT sequence, event_id, payload FROM events ORDER BY sequence").fetchall()
        events = []
        for index, (sequence, event_id, payload) in enumerate(records):
            if index >= len(self._events) or sequence != index + 1:
                raise ValueError("Journal is not a contiguous replay prefix")
            event = json.loads(payload)
            if event_id != self._events[index]["event_id"] or _dump(event) != _dump(self._events[index]):
                raise ValueError("Journal event differs from frozen replay")
            events.append(event)
        state = self._account(events)
        rows = self._connection.execute("SELECT payload FROM account WHERE id=1").fetchall()
        if len(rows) != 1 or _dump(json.loads(rows[0][0])) != _dump(state):
            raise ValueError("Stored account differs from event replay")
        return events, state

    def recover(self):
        """Verify and rebuild a consistent committed snapshot; never repair silently."""
        try:
            self._connection.execute("BEGIN")
            events, state = self._verify()
            self._connection.commit()
            return dict(applied_events=len(events), account=state)
        except BaseException:
            self._connection.rollback()
            raise

    def _write_account(self, state):
        self._connection.execute("UPDATE account SET payload=? WHERE id=1", (_dump(state),))

    def record(self, event):
        """Apply next event or ignore exact redelivery; conflicts/out-of-order fail."""
        if not isinstance(event, dict) or "event_id" not in event:
            raise ValueError("Finalized order event required")
        identity(event["event_id"])
        payload = _dump(event)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            events, state = self._verify()
            for index, prior in enumerate(events):
                if prior["event_id"] == event["event_id"]:
                    if _dump(prior) != payload:
                        raise ValueError("Conflicting content for existing event ID")
                    self._connection.commit()
                    return dict(status="duplicate", sequence=index + 1,
                                snapshot=dict(applied_events=len(events), account=state))
            index = len(events)
            if index >= len(self._events) or payload != _dump(self._events[index]):
                raise ValueError("Expected the next unchanged event from frozen replay")
            state = self._account(events + [event])
            self._connection.execute("INSERT INTO events VALUES(?, ?, ?)",
                                     (index + 1, event["event_id"], payload))
            self._write_account(state)
            self._connection.commit()
            return dict(status="applied", sequence=index + 1,
                        snapshot=dict(applied_events=index + 1, account=state))
        except BaseException:
            self._connection.rollback()
            raise

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
