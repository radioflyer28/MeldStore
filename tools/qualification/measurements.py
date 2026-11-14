"""Per-process instrumentation at public APIs; counts are not wire round trips."""

import time
from collections import Counter
from contextlib import contextmanager
from unittest.mock import patch

import obstore


class Measurements:
    def __init__(self, callback=lambda: None):
        self.callback = callback
        self.calls = Counter()
        self.write_depth = 0
        self.operations = []

    @contextmanager
    def operation(self, name, **details):
        before = self.calls.copy()
        start = time.perf_counter()
        result = {'name': name, **details, 'status': 'incomplete'}
        self.operations.append(result)
        self.callback()
        try:
            yield result
            result['status'] = 'passed'
        finally:
            result['seconds'] = time.perf_counter() - start
            result['calls'] = dict(self.calls - before)
            self.callback()

    @contextmanager
    def storage(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in ('get', 'get_range', 'head', 'put', 'copy', 'delete', 'list'):
                if not hasattr(obstore, name):
                    continue
                original = getattr(obstore, name)

                def counted(*args, _name=name, _original=original, **kwargs):
                    self.calls['storage.' + _name] += 1
                    if self.write_depth:
                        raise AssertionError('Payload I/O inside SQL write scope')
                    return _original(*args, **kwargs)

                stack.enter_context(patch.object(obstore, name, counted))
            yield

    @contextmanager
    def catalog(self, catalog):
        original = catalog.transaction

        @contextmanager
        def transaction(*args, **kwargs):
            write = kwargs.get('write', True)
            with original(*args, **kwargs) as tx:
                sql, many = tx.sql, tx.sql_many

                def counted_sql(*args, **kwargs):
                    self.calls['sql'] += 1
                    return sql(*args, **kwargs)

                def counted_many(statement, parameters):
                    self.calls['sql_many'] += 1

                    def rows():
                        for row in parameters:
                            self.calls['parameter_sets'] += 1
                            yield row
                    return many(statement, rows())

                tx.sql, tx.sql_many = counted_sql, counted_many
                self.write_depth += int(write)
                try:
                    yield tx
                finally:
                    self.write_depth -= int(write)
        with patch.object(catalog, 'transaction', transaction):
            yield


def latency(samples):
    if not samples:
        raise ValueError('No latency observations')
    ordered = sorted(samples)
    import math
    return {'unit': 'seconds', 'samples': len(samples),
            'p50': ordered[math.ceil(len(samples) * .50) - 1],
            'p95': ordered[math.ceil(len(samples) * .95) - 1]}
