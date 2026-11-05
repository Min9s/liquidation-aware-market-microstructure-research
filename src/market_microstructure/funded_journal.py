"""Durable synthetic commands and funded snapshots, without transport retries."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .execution_contracts import KIND, fields, identity
from .funded_orders import FundedOrderManager
from . import order_protocol as protocol

SCHEMA = "synthetic-funded-journal-v1"
MAX_JSON_BYTES = 8 * 1024**2


def _dump(value):
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Finite JSON journal facts required") from error
    if len(payload.encode()) > MAX_JSON_BYTES:
        raise ValueError("Funded journal record exceeds 8 MiB")
    return payload


def _load(payload):
    if not isinstance(payload, str) or len(payload.encode()) > MAX_JSON_BYTES:
        raise ValueError("Bounded JSON journal record required")
    value = json.loads(payload)
    if _dump(value) != payload:
        raise ValueError("Journal record is not canonical finite JSON")
    return value


def _definition_sha():
    names = ("funded_journal.py", "funded_orders.py", "order_reservations.py", "order_lifecycle.py",
             "order_protocol.py", "execution_contracts.py", "execution_risk.py")
    definitions = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                   for name in names}
    return hashlib.sha256(_dump(definitions).encode()).hexdigest()


def _initial_sha(initial):
    return hashlib.sha256(_dump(initial).encode()).hexdigest()


def _connect(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _reduce(manager, request):
    """Replay exactly one recorded local decision, response, or query result."""
    if not isinstance(request, dict):
        raise ValueError("Journal request mapping required")
    action = request.get("action")
    if action == "submit":
        fields(request, "action order now_ns marks")
        decision = manager.submit(request["order"], now_ns=request["now_ns"], marks=request["marks"])
        return {key: decision[key] for key in ("accepted", "reason", "order")}
    if action in ("request_cancel", "mark_unknown"):
        fields(request, "action client_order_id")
        identity(request["client_order_id"])
        getattr(manager, action)(request["client_order_id"])
        return None
    if action == "apply":
        fields(request, "action report")
        return manager.apply(request["report"])
    if action == "reconcile":
        fields(request, "action client_order_id report")
        client_id = request["client_order_id"]
        identity(client_id)
        record = manager.snapshot()["lifecycle"]["orders"].get(client_id)
        if record is None:
            raise ValueError("Cannot reconcile an unregistered order")
        if request["report"] is None:
            if record["status"] == "pending_new" or record["cancel_status"] == "pending":
                manager.mark_unknown(client_id)
            return dict(found=False, client_order_id=client_id,
                        unresolved=record["status"] not in protocol.TERMINAL)
        outcome = manager.apply(request["report"])
        if request["report"]["state"]["order"]["client_order_id"] != client_id:
            raise ValueError("Query response belongs to another order")
        return dict(found=True, client_order_id=client_id, outcome=outcome)
    raise ValueError("Unknown funded journal action")


class FundedJournal:
    """SQLite is the source of truth; every operation replays the committed log.

    Callers may send only a newly returned outgoing command. No sending, retry,
    timers, exchange authentication or exactly-once network delivery is provided.
    """

    def __init__(self, connection, metadata):
        self._connection = connection
        self._metadata = deepcopy(metadata)

    @classmethod
    def create(cls, path, instruments, policy, venue_id="synthetic-venue"):
        manager = FundedOrderManager(instruments, policy, venue_id)
        initial = manager.export_log()
        metadata = dict(source_kind=KIND, schema=SCHEMA, definition_sha256=_definition_sha(),
                        initial=initial, initial_sha256=_initial_sha(initial))
        metadata = _load(_dump(metadata))
        path = Path(path).absolute()
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        connection = None
        try:
            connection = _connect(path)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE metadata(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE commands(sequence INTEGER PRIMARY KEY, command_id TEXT NOT NULL UNIQUE, "
                               "request TEXT NOT NULL, result TEXT NOT NULL)")
            connection.execute("CREATE TABLE snapshot(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            connection.execute("INSERT INTO metadata VALUES(1, ?)", (_dump(metadata),))
            connection.execute("INSERT INTO snapshot VALUES(1, ?)", (_dump(manager.snapshot()),))
            connection.commit()
            return cls(connection, metadata)
        except BaseException:
            if connection is not None:
                connection.close()
            # Leave failed initialization visible; never overwrite or silently repair.
            raise

    @classmethod
    def open(cls, path):
        path = Path(path).absolute()
        if path.is_symlink():
            raise ValueError("Funded journal must be an ordinary database path")
        connection = _connect(path)
        try:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT payload FROM metadata WHERE id=1").fetchall()
            if len(rows) != 1:
                raise ValueError("Missing funded journal metadata")
            store = cls(connection, _load(rows[0][0]))
            store._verify()
            connection.commit()
            return store
        except BaseException:
            connection.close()
            raise

    def _verify(self):
        rows = self._connection.execute("SELECT payload FROM metadata WHERE id=1").fetchall()
        if len(rows) != 1 or rows[0][0] != _dump(self._metadata):
            raise ValueError("Funded journal metadata changed")
        metadata = self._metadata
        fields(metadata, "source_kind schema definition_sha256 initial initial_sha256")
        if (metadata["source_kind"] != KIND or metadata["schema"] != SCHEMA
                or metadata["definition_sha256"] != _definition_sha()
                or metadata["initial_sha256"] != _initial_sha(metadata["initial"])):
            raise ValueError("Funded journal schema or code definition changed")
        manager = FundedOrderManager.replay(metadata["initial"])
        if manager.export_log()["events"]:
            raise ValueError("Initial journal must not contain prior events")
        records = self._connection.execute(
            "SELECT sequence, command_id, request, result FROM commands ORDER BY sequence LIMIT ?",
            (protocol.MAX_EVENTS + 1,)).fetchall()
        if len(records) > protocol.MAX_EVENTS:
            raise ValueError("Funded journal command bound reached")
        for index, (sequence, command_id, request, result) in enumerate(records, 1):
            identity(command_id)
            if sequence != index:
                raise ValueError("Funded journal command sequence is not contiguous")
            actual = _reduce(manager, _load(request))
            if _dump(actual) != _dump(_load(result)):
                raise ValueError("Stored command result differs from replay")
        rows = self._connection.execute("SELECT payload FROM snapshot WHERE id=1").fetchall()
        if len(rows) != 1 or _dump(manager.snapshot()) != _dump(_load(rows[0][0])):
            raise ValueError("Funded snapshot differs from command replay")
        return manager, records

    def recover(self):
        """Consistent read, including query targets. pending_new never means unsent."""
        try:
            self._connection.execute("BEGIN")
            manager, records = self._verify()
            snapshot = manager.snapshot()
            targets = [dict(client_order_id=key, status=record["status"], cancel_status=record["cancel_status"])
                       for key, record in snapshot["lifecycle"]["orders"].items()
                       if record["status"] not in protocol.TERMINAL]
            self._connection.commit()
            return dict(committed_commands=len(records), snapshot=snapshot, reconciliation=targets)
        except BaseException:
            self._connection.rollback()
            raise

    def assess(self, *, now_ns, marks):
        try:
            self._connection.execute("BEGIN")
            manager, _ = self._verify()
            result = manager.assess(now_ns=now_ns, marks=marks)
            self._connection.commit()
            return result
        except BaseException:
            self._connection.rollback()
            raise

    def _write_snapshot(self, snapshot):
        self._connection.execute("UPDATE snapshot SET payload=? WHERE id=1", (_dump(snapshot),))

    def _record(self, command_id, request):
        identity(command_id)
        payload = _dump(request)
        request = _load(payload)  # Freeze caller inputs before beginning a transaction.
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            manager, records = self._verify()
            for sequence, prior_id, prior_request, prior_result in records:
                if prior_id == command_id:
                    if payload != prior_request:
                        raise ValueError("Conflicting request for committed command ID")
                    self._connection.commit()
                    return dict(status="duplicate", command_id=command_id, sequence=sequence,
                                result=_load(prior_result), outgoing=None)
            if len(records) >= protocol.MAX_EVENTS:
                raise ValueError("Funded journal command bound reached")
            result = _reduce(manager, request)
            sequence = len(records) + 1
            self._connection.execute("INSERT INTO commands VALUES(?, ?, ?, ?)",
                                     (sequence, command_id, payload, _dump(result)))
            self._write_snapshot(manager.snapshot())
            self._connection.commit()
            outgoing = None
            if request["action"] == "submit" and result["accepted"]:
                outgoing = result["order"]
            elif request["action"] == "request_cancel":
                outgoing = dict(source_kind=KIND, action="cancel", client_order_id=request["client_order_id"])
            return dict(status="committed", command_id=command_id, sequence=sequence,
                        result=deepcopy(result), outgoing=deepcopy(outgoing))
        except BaseException:
            self._connection.rollback()
            raise

    def submit(self, command_id, order, *, now_ns, marks):
        return self._record(command_id, dict(action="submit", order=order, now_ns=now_ns, marks=marks))

    def request_cancel(self, command_id, client_order_id):
        return self._record(command_id, dict(action="request_cancel", client_order_id=client_order_id))

    def mark_unknown(self, command_id, client_order_id):
        return self._record(command_id, dict(action="mark_unknown", client_order_id=client_order_id))

    def apply(self, command_id, report):
        return self._record(command_id, dict(action="apply", report=report))

    def reconcile(self, command_id, client_order_id, report):
        """Persist a query response or absence; absence never releases a reservation."""
        return self._record(command_id, dict(action="reconcile", client_order_id=client_order_id, report=report))

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
