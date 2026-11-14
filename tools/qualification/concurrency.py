"""Spawned independent clients; one catalog handle per process."""

import multiprocessing
import os
import time
import traceback
from contextlib import contextmanager

from meldstore import ConflictError
from tools.qualification.fixture import SCHEMA
from tools.qualification.measurements import Measurements, latency
from tools.qualification.workload import opened, require
from tools.qualify_local import peak_rss, windows_memory


def current_rss(pid):
    if os.name != 'nt':
        from pathlib import Path
        return int(Path(f'/proc/{pid}/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    return windows_memory(pid)[1]


@contextmanager
def admitted(config, startup_lock):
    # Initialization takes an exclusive coordinator lease. Admit clients explicitly
    # before starting concurrent timed operations; never retry a failed open.
    manager = opened(config)
    with startup_lock:
        store = manager.__enter__()
    try:
        yield store
    finally:
        manager.__exit__(None, None, None)


def expected_records(before, clients, winner):
    expected = []
    for number, old in enumerate(before):
        row = dict(old, metadata=dict(old['metadata']))
        if number < clients:
            row['version'] += 1
            row['metadata']['source_id'] = f'client-{number}'
        if number == 0:
            row['version'] += 1
            row['metadata']['source_id'] = f'winner-{winner}'
        expected.append(row)
    return expected


def client(config, number, before, clients, winner, barrier, startup_lock, pipe):
    try:
        metrics = Measurements()
        with admitted(config, startup_lock) as store, metrics.catalog(store.catalog), metrics.storage():
            ids = [row['id'] for row in before]
            own = before[number]
            store.update_metadata(ids[number], schema=SCHEMA, expected_version=own['version'],
                                  changes={'source_id': f'client-{number}'})
            barrier.wait(timeout=60)
            shared = store.stat(ids[0])
            barrier.wait(timeout=60)
            success, conflict = 0, 0
            try:
                store.update_metadata(ids[0], schema=SCHEMA, expected_version=shared['version'],
                                      changes={'source_id': f'winner-{number}'})
                winner.value = number
                success = 1
            except ConflictError:
                conflict = 1
            barrier.wait(timeout=60)
            require(0 <= winner.value < clients, 'Missing successful edit')
            expected = expected_records(before, clients, winner.value)
            samples = []
            for n in range(105):
                started = time.perf_counter()
                rows = store.find(schema=SCHEMA, limit=1000)
                elapsed = time.perf_counter() - started
                require(rows == expected, 'Concurrent read oracle mismatch')
                if n >= 5:
                    samples.append(elapsed)
            require(not any(k.startswith('storage.') for k in metrics.calls),
                    'Concurrent metadata accessed storage')
            pipe.send({'status': 'passed', 'client': number, 'shared_success': success,
                       'shared_conflict': conflict, 'disjoint_success': 1,
                       'latency': latency(samples), 'warmup': 5, 'calls': dict(metrics.calls),
                       'peak_rss_bytes': peak_rss()})
    except BaseException as exc:
        # No exception message/locals: DSNs and private paths must not enter evidence.
        pipe.send({'status': 'failed', 'error_type': type(exc).__name__,
                   'frames': [f.name for f in traceback.extract_tb(exc.__traceback__)]})
        raise
    finally:
        pipe.close()


def concurrent(config, manifest, metrics, budget):
    context = multiprocessing.get_context('spawn')
    ids = [e['id'] for e in manifest['files']]
    for count in manifest['profile']['clients']:
        require(len(ids) >= count, 'Corpus needs at least as many blobs as clients')
        with opened(config) as store:
            before = [store.stat(id) for id in ids]
        with metrics.operation('concurrent_clients', clients=count) as result:
            barrier = context.Barrier(count)
            winner = context.Value('i', -1)
            startup_lock = context.Lock()
            processes, readers = [], []
            try:
                for number in range(count):
                    reader, writer = context.Pipe(duplex=False)
                    process = context.Process(target=client,
                                              args=(config, number, before, count, winner, barrier, startup_lock, writer))
                    process.start()
                    writer.close()
                    processes.append(process)
                    readers.append(reader)
                max_memory = 0
                while any(p.is_alive() for p in processes):
                    budget.check()
                    sample = current_rss(os.getpid())
                    for process in processes:
                        if process.is_alive():
                            try:
                                sample += current_rss(process.pid)
                            except (OSError, FileNotFoundError):
                                if process.is_alive():
                                    raise
                    max_memory = max(max_memory, sample)
                    if sample > budget.memory_bytes:
                        raise RuntimeError('Aggregate client memory limit exceeded')
                    time.sleep(.05)
                results = [reader.recv() for reader in readers if reader.poll()]
                require(len(results) == count, 'Concurrent client did not return evidence')
                require(all(p.exitcode == 0 for p in processes), 'Concurrent worker failed')
                require(all(r['status'] == 'passed' for r in results), 'Concurrent operation failed')
                require(sum(r['shared_success'] for r in results) == 1, 'Lost-update guard failed')
                require(sum(r['shared_conflict'] for r in results) == count - 1, 'Conflict count differs')
                result.update(results=results, sampled_aggregate_rss_bytes=max_memory,
                              sample_interval_seconds=.05, attempts=count * 2,
                              successes=count + 1, conflicts=count - 1, errors=0)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=10)
                for reader in readers:
                    reader.close()
        with opened(config) as store:
            require([store.stat(id) for id in ids] == expected_records(before, count, winner.value),
                    'Persisted concurrent metadata and versions differ')
