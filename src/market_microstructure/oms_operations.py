"""Opt-in operational journal; all decisions replay in the funded transaction.

No timer, network adapter or notification transport. Caller supplies logical times
and a complete authoritative cumulative query, including a deadline for escalation.
"""
from copy import deepcopy

from .execution_contracts import fields, identity, stamp
from .funded_orders import FundedOrderManager
from .funded_journal import FundedJournal, _reduce
from . import order_protocol as protocol


class OperationsManager:
    def __init__(self, instruments, policy, venue_id='synthetic-venue'):
        self.funded = FundedOrderManager(instruments, policy, venue_id)
        self.ops = dict(mode='RECOVERY', clock_ns=0, epoch=0, verified={}, incidents={},
                        last_reconciliation=None, transitions=[])

    def __getattr__(self, name):
        # Only used by the existing journal reducer for ordinary funded actions.
        if name.startswith('__'):
            raise AttributeError(name)
        return getattr(self.funded, name)

    def snapshot(self):
        return dict(self.funded.snapshot(), operations=deepcopy(self.ops))

    def export_log(self):
        bundle = self.funded.export_log()
        bundle['schema'] = 'operational-funded-initial-v1'
        return bundle

    @classmethod
    def replay(cls, bundle):
        if bundle.get('schema') != 'operational-funded-initial-v1' or bundle.get('events') != []:
            raise ValueError('Empty operational initial state required')
        initial = deepcopy(bundle)
        initial['schema'] = 'funded-orders-log-v1'
        funded = FundedOrderManager.replay(initial)
        manager = cls(initial['instruments'], initial['policy'], initial['venue_id'])
        manager.funded = funded
        return manager

    def time(self, now):
        stamp(now)
        if now < self.ops['clock_ns']:
            raise ValueError('Operational clock cannot move backwards')
        self.ops['clock_ns'] = now

    def mode(self, target, reason):
        if self.ops['mode'] != target:
            if target == 'RESTRICTED':
                self.ops['epoch'] += 1
                self.ops['verified'] = {}
            self.ops['transitions'].append(dict(at_ns=self.ops['clock_ns'], previous=self.ops['mode'],
                                                mode=target, reason=reason))
            self.ops['mode'] = target

    def comparison(self, client_id, report):
        identity(client_id)
        local = self.funded._manager._orders.get(client_id)
        if local is None:
            raise ValueError('Cannot inspect an unregistered order')
        if report is None:
            return 'query_unavailable', local, None
        # Malformed/wrong-order queries are caller errors, not trusted evidence.
        report = protocol.report(report, self.funded._manager.instruments, self.funded._manager.venue_id)
        if report['state']['order'] != local['order']:
            raise ValueError('Query response belongs to another order')
        try:
            trial = deepcopy(self.funded)
            trial.apply(report)
        except ValueError as error:
            return 'conflicting_facts', local, str(error)
        state = report['state']
        if local['state'] != state:
            return 'state_mismatch', local, None
        if local['cancel_status'] in {'pending', 'unknown'}:
            return 'cancel_unconfirmed', local, None
        return None, local, None

    def incident(self, client_id, kind, local, report, reason):
        # One unresolved incident per order+type; new episodes get new IDs.
        existing = next((i for i in self.ops['incidents'].values()
                         if i['client_order_id'] == client_id and i['type'] == kind
                         and i['status'] != 'RESOLVED'), None)
        if existing is None:
            key = f"incident-{len(self.ops['incidents']) + 1}"
            existing = dict(incident_id=key, client_order_id=client_id, type=kind, severity='HIGH',
                            status='OPEN', occurred_ns=self.ops['clock_ns'], local=deepcopy(local),
                            exchange=deepcopy(report), execution_ids=[] if report is None else
                            [f['execution_id'] for f in report['state']['fills']],
                            reason=reason, resolved_ns=None, resolution=None,
                            history=[dict(status='OPEN', at_ns=self.ops['clock_ns'], reason=reason)])
            self.ops['incidents'][key] = existing
        self.ops['verified'].pop(client_id, None)
        self.mode('RESTRICTED', existing['incident_id'])
        return existing['incident_id']


def reduce_operations(manager, request):
    action = request.get('action')
    if action == 'session_start':
        fields(request, 'action session_id now_ns pid')
        identity(request['session_id'])
        previous = manager.ops.get('session')
        reduce_operations(manager, dict(action='control', mode='RECOVERY', now_ns=request['now_ns'], reason='startup-recovery'))
        manager.ops['session'] = dict(session_id=request['session_id'], pid=request['pid'], clean=False,
                                     previous_exit='clean' if previous and previous['clean'] else 'unclean-or-first-start')
        return deepcopy(manager.ops['session'])
    if action == 'session_end':
        fields(request, 'action session_id')
        if manager.ops['session']['session_id'] != request['session_id']:
            raise ValueError('Session ownership changed')
        manager.ops['session']['clean'] = True
        return dict(clean=True)
    if action == 'submit':
        manager.time(request['now_ns'])
        trial = deepcopy(manager.funded)
        decision = _reduce(trial, request)  # All original input, marks and funding validation.
        if manager.ops['mode'] != 'NORMAL' and request['order']['side'] == 'buy':
            manager.funded._last_decision_ns = request['now_ns']
            return dict(accepted=False, reason='operational_' + manager.ops['mode'].lower(), order=None)
        manager.funded = trial
        manager.ops['verified'].pop(request['order']['client_order_id'], None)
        return decision
    if action in {'inspect', 'operational_reconcile'}:
        fields(request, 'action client_order_id report now_ns deadline_ns')
        manager.time(request['now_ns'])
        stamp(request['deadline_ns'])
        client_id, report = request['client_order_id'], request['report']
        kind, local, error = manager.comparison(client_id, report)
        if action == 'operational_reconcile' and kind != 'conflicting_facts':
            _reduce(manager.funded, dict(action='reconcile', client_order_id=client_id, report=report))
            kind, local, error = manager.comparison(client_id, report)
        if kind:
            # Conflict is immediate; delivery lag/unavailability requires explicit expiry.
            escalated = kind == 'conflicting_facts' or request['now_ns'] >= request['deadline_ns']
            incident_id = (manager.incident(client_id, kind, local, report,
                           error or 'Authoritative comparison unresolved after supplied deadline') if escalated else None)
            manager.ops['verified'].pop(client_id, None)
            return dict(matched=False, kind=kind, escalated=escalated, incident_id=incident_id)
        manager.ops['verified'][client_id] = manager.ops['epoch']
        manager.ops['last_reconciliation'] = dict(client_order_id=client_id, at_ns=request['now_ns'],
                                                  event_id=report['event_id'])
        # A conflicting authority requires operator investigation, never silent closure.
        for incident in manager.ops['incidents'].values():
            if incident['client_order_id'] != client_id or incident['status'] == 'RESOLVED':
                continue
            if incident['type'] == 'conflicting_facts' and incident['status'] != 'INVESTIGATING':
                continue
            incident.update(status='RESOLVED', resolved_ns=request['now_ns'],
                            resolution='Authoritative cumulative query matched validated local facts')
            incident['history'].append(dict(status='RESOLVED', at_ns=request['now_ns'], reason=incident['resolution']))
        return dict(matched=True, event_id=report['event_id'])
    if action == 'control':
        fields(request, 'action mode now_ns reason')
        manager.time(request['now_ns'])
        identity(request['reason'])
        target = request['mode']
        if target == 'RECOVERY':
            manager.ops['epoch'] += 1
            manager.ops['verified'] = {}
        elif target == 'NORMAL':
            if any(i['status'] != 'RESOLVED' for i in manager.ops['incidents'].values()):
                raise ValueError('Unresolved incidents prevent NORMAL')
            for key in manager.funded.snapshot()['lifecycle']['orders']:
                if manager.ops['verified'].get(key) != manager.ops['epoch']:
                    raise ValueError('Every registered order requires a fresh matched query')
        elif target != 'RESTRICTED':
            raise ValueError('Unknown operational mode')
        manager.mode(target, request['reason'])
        return dict(mode=target)
    if action == 'investigate':
        fields(request, 'action incident_id now_ns reason')
        manager.time(request['now_ns'])
        identity(request['reason'])
        incident = manager.ops['incidents'][request['incident_id']]
        if incident['status'] == 'RESOLVED':
            raise ValueError('Resolved incident cannot be investigated')
        if incident['status'] != 'INVESTIGATING':
            incident['status'] = 'INVESTIGATING'
            incident['history'].append(dict(status='INVESTIGATING', at_ns=request['now_ns'], reason=request['reason']))
        return dict(incident_id=incident['incident_id'], status=incident['status'])
    if action == 'reconcile':
        raise ValueError('Use reconcile_at with explicit operational time and deadline')
    result = _reduce(manager.funded, request)
    # Any local mutation or newly seen report requires a fresh query before NORMAL.
    client_id = (request['report']['state']['order']['client_order_id'] if action == 'apply'
                 else request.get('client_order_id'))
    manager.ops['verified'].pop(client_id, None)
    return result


class OperationalJournal(FundedJournal):
    """Separate opt-in schema; funded journal transaction and replay are reused."""
    _manager_type = OperationsManager
    _reducer = staticmethod(reduce_operations)
    _schema = 'synthetic-operational-journal-v1'

    def inspect(self, command_id, client_order_id, report, *, now_ns, deadline_ns):
        return self._record(command_id, dict(action='inspect', client_order_id=client_order_id,
                            report=report, now_ns=now_ns, deadline_ns=deadline_ns))

    def reconcile_at(self, command_id, client_order_id, report, *, now_ns, deadline_ns):
        return self._record(command_id, dict(action='operational_reconcile', client_order_id=client_order_id,
                            report=report, now_ns=now_ns, deadline_ns=deadline_ns))

    def control(self, command_id, mode, *, now_ns, reason):
        return self._record(command_id, dict(action='control', mode=mode, now_ns=now_ns, reason=reason))

    def investigate(self, command_id, incident_id, *, now_ns, reason):
        return self._record(command_id, dict(action='investigate', incident_id=incident_id, now_ns=now_ns, reason=reason))

    def diagnostics(self, client_order_id=None):
        # One consistent SQLite read, not separate recover + log reads.
        try:
            self._connection.execute('BEGIN')
            manager, records = self._verify()
            snapshot = manager.snapshot()
            orders = snapshot['lifecycle']['orders']
            from .funded_journal import _load
            events = [dict(sequence=seq, command_id=key, request=_load(req), result=_load(result))
                      for seq, key, req, result in records]
            if client_order_id is not None:
                events = [e for e in events if (e['request'].get('client_order_id') == client_order_id
                          or e['request'].get('order', {}).get('client_order_id') == client_order_id
                          or (e['request'].get('report') or {}).get('state', {}).get('order', {}).get('client_order_id') == client_order_id
                          or snapshot['operations']['incidents'].get(e['request'].get('incident_id'), {}).get('client_order_id') == client_order_id)]
            self._connection.commit()
            return dict(snapshot=snapshot, mode=snapshot['operations']['mode'],
                        unresolved_incidents=sum(i['status'] != 'RESOLVED' for i in snapshot['operations']['incidents'].values()),
                        active_orders=[k for k, v in orders.items() if v['status'] not in protocol.TERMINAL],
                        unconfirmed_orders=[k for k, v in orders.items() if v['status'] in {'pending_new', 'unknown'}
                                            or v['cancel_status'] in {'pending', 'unknown'}],
                        last_successful_reconciliation=snapshot['operations']['last_reconciliation'],
                        recovery_required=[k for k in orders if snapshot['operations']['verified'].get(k) != snapshot['operations']['epoch']],
                        recent_events=events[-30:])
        except BaseException:
            self._connection.rollback()
            raise


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description='Local synthetic OMS diagnostics and operator controls')
    parser.add_argument('database')
    parser.add_argument('--order', help='exact client order ID filter for recent journal commands')
    parser.add_argument('--mode', choices=['NORMAL', 'RESTRICTED', 'RECOVERY'])
    parser.add_argument('--investigate', metavar='INCIDENT_ID')
    parser.add_argument('--command-id')
    parser.add_argument('--now-ns', type=int)
    parser.add_argument('--reason')
    args = parser.parse_args()
    if args.mode and args.investigate:
        parser.error('choose mode or investigate')
    if (args.mode or args.investigate) and (not args.command_id or args.now_ns is None or not args.reason):
        parser.error('mutations require --command-id, --now-ns and --reason')
    with OperationalJournal.open(args.database) as store:
        if args.mode:
            store.control(args.command_id, args.mode, now_ns=args.now_ns, reason=args.reason)
        if args.investigate:
            store.investigate(args.command_id, args.investigate, now_ns=args.now_ns, reason=args.reason)
        print(json.dumps(store.diagnostics(args.order), indent=2))


if __name__ == '__main__':
    main()
