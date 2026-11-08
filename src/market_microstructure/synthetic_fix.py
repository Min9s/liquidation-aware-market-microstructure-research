"""In-process FIX field fixture; manual archive redelivery is not FIX Resend."""
from copy import deepcopy

from .simulated_venue import SimulatedVenue
from .fix_adapter import parse_new, execution_fields, cancel_request


class SyntheticFixVenue:
    def __init__(self, instruments):
        self.venue = SimulatedVenue(instruments)
        self.archive = []
        self.cancels = {}

    def emit(self, report, **kwargs):
        msg = execution_fields(report, **kwargs)
        self.archive.append(deepcopy(msg))
        return msg

    def new(self, message):
        return self.emit(self.venue.submit(parse_new(message, self.venue.instruments)))

    def execute(self, client_id, quantity, price):
        return self.emit(self.venue.execute(client_id, quantity, price))

    def request_cancel(self, message):
        client_id = message['41']
        spec = self.venue.query(client_id)['state']['order']
        if message != cancel_request(spec, message['11']):
            raise ValueError('Cancel binding mismatch')
        self.cancels[client_id] = message['11']
        return self.emit(self.venue.query(client_id), cancel_id=message['11'], pending=True)

    def confirm_cancel(self, client_id):
        report = self.venue.cancel(client_id)
        if report['report_type'] == 'cancel_rejected':
            state = report['state']
            return {'35':'9','11':self.cancels[client_id], '41':client_id, '37':state['exchange_order_id'],
                    '39':'2' if state['status'] == 'filled' else '4', '434':'1', '102':'0'}
        return self.emit(report, cancel_id=self.cancels[client_id])

    def query(self, client_id):
        report = self.venue.query(client_id)
        if report is None:
            return None
        msg = execution_fields(report)
        msg.update({'150':'I', '17':'0'})
        return msg

    def redeliver(self):
        return deepcopy(self.archive)
