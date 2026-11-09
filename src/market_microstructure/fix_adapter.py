"""Restricted FIX 4.4 business-field profile. No wire encoding or FIX session."""
from copy import deepcopy
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from hashlib import sha256

from .execution_contracts import KIND, identity, number
from . import order_protocol as protocol
from .oms_operations import OperationalJournal, reduce_operations

STAMP = '20260101-00:00:00.000'  # Fixture business time, never a session SendingTime.
# Revisions are 10 * cumulative lots + rank (1..4), strictly below 2**63.
_MAX_ORDER_LOTS = (2**63 - 1 - 4) // 10


def _fix_order(value, instruments):
    spec = protocol.order(value, instruments)
    with localcontext() as context:
        context.prec = 128
        lots = Decimal(spec['quantity']) / Decimal(instruments[spec['symbol']]['quantity_step'])
        if lots > _MAX_ORDER_LOTS:
            raise ValueError('Order quantity exceeds FIX revision capacity')
    return spec


def _event_id(namespace: str, value: str) -> str:
    identity(value)
    return namespace + '-' + sha256(value.encode()).hexdigest()


def new_order(order):
    return {'35': 'D', '11': order['client_order_id'], '55': order['symbol'],
            '54': '1' if order['side'] == 'buy' else '2', '38': order['quantity'],
            '40': '2', '44': order['limit_price'], '59': '0', '60': STAMP}


def parse_new(message, instruments):
    if set(message) != {'35', '11', '55', '54', '38', '40', '44', '59', '60'} or message['35'] != 'D' or message['40'] != '2' or message['59'] != '0':
        raise ValueError('Only limit DAY NewOrderSingle field profile supported')
    if message['54'] not in {'1', '2'} or message['60'] != STAMP:
        raise ValueError('Unsupported side or fixture business time')
    return _fix_order(dict(source_kind=KIND, client_order_id=message['11'], symbol=message['55'],
        side='buy' if message['54'] == '1' else 'sell', quantity=message['38'], limit_price=message['44']), instruments)


def cancel_request(order, cancel_id):
    identity(cancel_id)
    return {'35': 'F', '11': cancel_id, '41': order['client_order_id'], '55': order['symbol'],
            '54': '1' if order['side'] == 'buy' else '2', '38': order['quantity'], '60': STAMP}


def average(fills):
    with localcontext() as ctx:
        ctx.prec = 128
        qty = sum((Decimal(f['quantity']) for f in fills), Decimal(0))
        return format((sum((Decimal(f['quantity'])*Decimal(f['price']) for f in fills), Decimal(0))/qty
                       if qty else Decimal(0)).quantize(Decimal('.00000001'), rounding=ROUND_HALF_EVEN), 'f')


def execution_fields(report, *, cancel_id=None, pending=False):
    state = report['state']
    spec = state['order']
    fills = state['fills']
    qty = sum((Decimal(f['quantity']) for f in fills), Decimal(0))
    statuses = {'open': '0', 'partially_filled': '1', 'filled': '2', 'canceled': '4', 'rejected': '8'}
    event = {'accepted': '0', 'trade': 'F', 'canceled': '4', 'rejected': '8', 'snapshot': 'I'}[report['report_type']]
    msg = {'35': '8', '11': spec['client_order_id'], '37': state['exchange_order_id'],
           '17': fills[-1]['execution_id'] if event == 'F' else 'status-' + report['event_id'],
           '150': '6' if pending else event, '39': '6' if pending else statuses[state['status']],
           '55': spec['symbol'], '54': '1' if spec['side'] == 'buy' else '2', '38': spec['quantity'],
           '40': '2', '44': spec['limit_price'], '14': str(qty),
           '151': '0' if state['status'] in protocol.TERMINAL else str(Decimal(spec['quantity'])-qty),
           '6': average(fills), '32': fills[-1]['quantity'] if event == 'F' and not pending else '0',
           '31': fills[-1]['price'] if event == 'F' and not pending else '0'}
    if pending or event == '4':
        if not cancel_id:
            raise ValueError('Cancel response requires request ClOrdID')
        msg.update({'11': cancel_id, '41': spec['client_order_id']})
    return msg


def decode_history(spec, messages, cancel_ids, instruments, venue_id):
    with localcontext() as context:
        context.prec = 128
        return _decode_history(spec, messages, cancel_ids, instruments, venue_id)


def _decode_history(spec, messages, cancel_ids, instruments, venue_id):
    """Build cumulative facts only from actual trade ExecIDs; detect missing prefix."""
    expected = new_order(spec)
    ordered = []
    binding = None
    for msg in messages:
        required = {'35','11','37','17','150','39','55','54','38','40','44','14','151','6','32','31'}
        if not required <= set(msg) or set(msg) - required - {'41'} or msg['35'] != '8':
            raise ValueError('Unsupported ExecutionReport fields')
        if not all(isinstance(v, str) for v in msg.values()):
            raise ValueError('FIX field strings required')
        for tag in ('37', '17'):
            identity(msg[tag])
        if msg['17'] == '0' and msg['150'] != 'I':
            raise ValueError('ExecID=0 is reserved for status queries')
        if binding is not None and binding != msg['37']:
            raise ValueError('OrderID binding changed')
        binding = msg['37']
        if msg['11'] != spec['client_order_id']:
            if cancel_ids.get(msg['11']) != spec['client_order_id'] or msg.get('41') != spec['client_order_id']:
                raise ValueError('Unknown cancel ClOrdID/OrigClOrdID')
        elif '41' in msg:
            raise ValueError('Unexpected OrigClOrdID')
        for tag in ('55','54','38','40','44'):
            if msg[tag] != expected[tag]:
                raise ValueError('FIX order binding changed')
        event, status = msg['150'], msg['39']
        allowed = {'0': {'0'}, 'F': {'1','2','6'}, '6': {'6'}, '4': {'4'}, '8': {'8'}, 'I': {'0','1','2','4','6','8'}}
        if event in {'6','4'} and cancel_ids.get(msg['11']) != spec['client_order_id']:
            raise ValueError('Cancel event requires cancel ClOrdID and OrigClOrdID')
        if event not in allowed or status not in allowed[event]:
            raise ValueError('Unsupported ExecType/OrdStatus pair')
        cum = number(msg['14'])
        leaves = number(msg['151'])
        if cum % Decimal(instruments[spec['symbol']]['quantity_step']):
            raise ValueError('CumQty lot mismatch')
        if cum < 0 or leaves < 0 or cum > Decimal(spec['quantity']):
            raise ValueError('Invalid cumulative quantities')
        terminal = status in {'2','4','8'}
        if leaves != (0 if terminal else Decimal(spec['quantity']) - cum):
            raise ValueError('LeavesQty disagrees with current state')
        if event != 'F' and (number(msg['32']) != 0 or number(msg['31']) != 0):
            raise ValueError('Non-trade cannot add an execution')
        if status == '2' and cum != Decimal(spec['quantity']) or status in {'0','8'} and cum != 0 or status == '1' and not 0 < cum < Decimal(spec['quantity']):
            raise ValueError('OrdStatus disagrees with CumQty')
        if status == '6' and cum == Decimal(spec['quantity']):
            raise ValueError('Fully executed Pending Cancel is outside this profile')
        if status == '6' and spec['client_order_id'] not in cancel_ids.values():
            raise ValueError('Pending Cancel without registered cancel request')
        number(msg['6'])
        if event == 'F':
            # Validate standalone trade facts before a gap can persist the message.
            single_quantity = number(msg['32'], positive=True)
            probe = dict(venue_id=venue_id, exchange_order_id=msg['37'], revision=1,
                         status='filled' if single_quantity == Decimal(spec['quantity']) else 'partially_filled',
                         order=spec, fills=[dict(execution_id=msg['17'], quantity=msg['32'], price=msg['31'])],
                         rejection_reason=None)
            protocol.state(probe, instruments, venue_id)
        rank = ({'0': 1, 'F': 2, '6': 3, '4': 4, '8': 4}[event] if event != 'I' else
                {'0':1, '1':2, '2':2, '4':4, '6':3, '8':4}[status])
        ordered.append((cum, rank, event == 'I', msg['17'], msg))
    fills = []
    reports = []
    for cum, rank, _, _, msg in sorted(ordered):
        event = msg['150']
        if event == 'F':
            quantity, price = number(msg['32'], positive=True), number(msg['31'], positive=True)
            expected_cum = sum((Decimal(f['quantity']) for f in fills), Decimal(0)) + quantity
            if expected_cum > cum:
                raise ValueError('Trade CumQty contradicts execution ledger')
            if expected_cum < cum:
                return reports, True
            fills.append(dict(execution_id=msg['17'], quantity=msg['32'], price=msg['31']))
        elif sum((Decimal(f['quantity']) for f in fills), Decimal(0)) != cum:
            return reports, True
        if number(msg['6']) != Decimal(average(fills)):
            raise ValueError('AvgPx disagrees with known execution facts')
        status = ('canceled' if msg['39'] == '4' else 'rejected' if msg['39'] == '8' else
                  'filled' if cum == Decimal(spec['quantity']) else 'partially_filled' if cum else 'open')
        step = Decimal(instruments[spec['symbol']]['quantity_step'])
        revision = int(cum / step) * 10 + rank
        state = dict(venue_id=venue_id, exchange_order_id=msg['37'], revision=revision, status=status,
                     order=spec, fills=deepcopy(fills), rejection_reason='fix-new-reject' if msg['39'] == '8' else None)
        report = dict(source_kind=KIND, event_id=_event_id('fix-er', msg['17']), state=state,
                      report_type={'0':'accepted','F':'trade','6':'snapshot','4':'canceled','8':'rejected','I':'snapshot'}[event])
        protocol.report(report, instruments, venue_id)
        reports.append(report)
    return reports, False


def reduce_fix(manager, request):
    action = request.get('action')
    if action == 'submit':
        # Reject before operational/funding state changes, including during replay.
        _fix_order(request['order'], manager.funded._manager.instruments)
        if request['order']['client_order_id'] in manager.ops.get('fix', {}).get('cancels', {}):
            raise ValueError('New ClOrdID collides with cancel request ID')
    if action not in {'fix_receive', 'fix_cancel', 'fix_query', 'fix_cancel_reject'}:
        if action == 'control' and request['mode'] == 'NORMAL' and manager.ops.get('fix', {}).get('gaps'):
            raise ValueError('Missing FIX execution facts prevent NORMAL')
        return reduce_operations(manager, request)
    book = manager.ops.setdefault('fix', dict(messages={}, cancels={}, gaps=[]))
    if action == 'fix_cancel':
        from .execution_contracts import fields
        fields(request, 'action client_order_id cancel_id')
        identity(request['cancel_id'])
        if request['cancel_id'] in book['cancels'] or request['cancel_id'] in manager.funded.snapshot()['lifecycle']['orders']:
            raise ValueError('Cancel ClOrdID reused')
        reduce_operations(manager, dict(action='request_cancel', client_order_id=request['client_order_id']))
        spec = manager.funded._manager._orders[request['client_order_id']]['order']
        book['cancels'][request['cancel_id']] = request['client_order_id']
        return dict(fields=cancel_request(spec, request['cancel_id']))
    if action == 'fix_cancel_reject':
        from .execution_contracts import fields
        fields(request, 'action message reject_id')
        msg = request['message']
        if set(msg) != {'35','11','41','37','39','434','102'} or msg['35'] != '9' or msg['434'] != '1' or msg['102'] != '0':
            raise ValueError('Only terminal too-late CancelReject supported')
        if book['cancels'].get(msg['11']) != msg['41']:
            raise ValueError('CancelReject request binding mismatch')
        local = manager.funded._manager._orders[msg['41']]
        if local['status'] not in {'filled', 'canceled'} or local['state']['exchange_order_id'] != msg['37'] or msg['39'] != ('2' if local['status'] == 'filled' else '4'):
            raise ValueError('CancelReject requires prior terminal execution facts')
        return reduce_operations(manager, dict(action='apply', report=dict(source_kind=KIND,
            event_id=_event_id('fix-reject', request['reject_id']), report_type='cancel_rejected', state=deepcopy(local['state']))))
    if action == 'fix_query':
        from .execution_contracts import fields
        fields(request, 'action message query_id now_ns deadline_ns')
        msg = deepcopy(request['message'])
        identity(request['query_id'])
        if msg.get('150') != 'I' or msg.get('17') != '0':
            raise ValueError('Authoritative query requires ExecType=I, ExecID=0')
        client_id = book['cancels'].get(msg.get('11'), msg.get('11'))
        spec = manager.funded._manager._orders[client_id]['order']
        messages = [m for m in book['messages'].values() if book['cancels'].get(m['11'], m['11']) == client_id]
        reports, gap = decode_history(spec, messages + [msg], book['cancels'], manager.funded._manager.instruments,
                                      manager.funded._manager.venue_id)
        report = next((r for r in reports if r['event_id'] == _event_id('fix-er', '0')), None)
        if not gap and report is not None:
            report['event_id'] = _event_id('fix-query', request['query_id'])
        return reduce_operations(manager, dict(action='operational_reconcile', client_order_id=client_id,
            report=None if gap else report, now_ns=request['now_ns'], deadline_ns=request['deadline_ns']))
    from .execution_contracts import fields
    fields(request, 'action message')
    msg = deepcopy(request['message'])
    if not isinstance(msg, dict) or msg.get('35') != '8':
        raise ValueError('Only ExecutionReport supported here')
    client_id = book['cancels'].get(msg.get('11'), msg.get('11'))
    if client_id not in manager.funded._manager._orders:
        raise ValueError('Unknown ClOrdID')
    if msg.get('150') == 'I':
        raise ValueError('Status queries must use reconcile_fix')
    identity(msg.get('17'))
    prior = book['messages'].get(msg['17'])
    if prior is not None and prior != msg:
        raise ValueError('Conflicting FIX ExecID')
    book['messages'][msg['17']] = msg
    spec = manager.funded._manager._orders[client_id]['order']
    messages = [m for m in book['messages'].values() if book['cancels'].get(m['11'], m['11']) == client_id]
    reports, gap = decode_history(spec, messages, book['cancels'], manager.funded._manager.instruments,
                                  manager.funded._manager.venue_id)
    for report in reports:
        reduce_operations(manager, dict(action='apply', report=report))
    book['gaps'] = sorted(set(book['gaps']) | {client_id}) if gap else [k for k in book['gaps'] if k != client_id]
    if gap:
        reduce_operations(manager, dict(action='control', mode='RECOVERY', now_ns=manager.ops['clock_ns'], reason='missing-fix-trades'))
    return dict(buffered=gap, duplicate=prior is not None, client_order_id=client_id)


class FixJournal(OperationalJournal):
    _reducer = staticmethod(reduce_fix)
    _schema = 'synthetic-fix-field-journal-v1'

    def receive(self, command_id, message):
        return self._record(command_id, dict(action='fix_receive', message=message))

    def cancel_fix(self, command_id, client_order_id, cancel_id):
        result = self._record(command_id, dict(action='fix_cancel', client_order_id=client_order_id, cancel_id=cancel_id))
        result['outgoing'] = result['result']['fields'] if result['status'] == 'committed' else None
        return result

    def reconcile_fix(self, command_id, message, *, now_ns, deadline_ns):
        return self._record(command_id, dict(action='fix_query', message=message, query_id=command_id,
                            now_ns=now_ns, deadline_ns=deadline_ns))

    def cancel_rejected(self, command_id, message):
        return self._record(command_id, dict(action='fix_cancel_reject', message=message, reject_id=command_id))
