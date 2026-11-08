"""Cooperating POSIX single-owner execution entry point; not distributed fencing."""
import os
from pathlib import Path
import uuid

from .oms_operations import OperationalJournal


class TradingSession:
    def __init__(self, journal, descriptor):
        self.journal = journal
        self._descriptor = descriptor
        self._pid = os.getpid()

    @classmethod
    def open(cls, path, *, journal_type=OperationalJournal):
        import fcntl
        path = Path(path).absolute()
        if path.is_symlink():
            raise ValueError('Ordinary database path required')
        path = path.resolve(strict=True)
        if path.stat().st_nlink != 1:
            raise ValueError('Hard-linked databases are unsupported')
        descriptor = os.open(str(path) + '.owner.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        journal = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = journal_type.open(path)
            session = cls(journal, descriptor)
            prior = journal.recover()['snapshot']['operations']
            session.session_id = 'session-' + uuid.uuid4().hex
            journal._record(session.session_id, dict(action='session_start', session_id=session.session_id,
                now_ns=prior['clock_ns'], pid=os.getpid()))
            return session
        except BaseException:
            if journal is not None:
                journal.close()
            os.close(descriptor)
            raise

    @classmethod
    def create(cls, path, instruments, policy, *, journal_type=OperationalJournal):
        with journal_type.create(path, instruments, policy):
            pass
        return cls.open(path, journal_type=journal_type)

    def __getattr__(self, name):
        value = getattr(self.journal, name)
        if callable(value):
            def checked(*args, **kwargs):
                if os.getpid() != self._pid or self._descriptor is None:
                    raise RuntimeError('Closed or foreign-process trading session')
                return value(*args, **kwargs)
            return checked
        return value

    def close(self):
        if self._descriptor is None:
            return
        if os.getpid() != self._pid:
            raise RuntimeError('Foreign-process session cannot close owner')
        try:
            self.journal._record(self.session_id + '-close', dict(action='session_end', session_id=self.session_id))
        finally:
            self.journal.close()
            os.close(self._descriptor)
            self._descriptor = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
