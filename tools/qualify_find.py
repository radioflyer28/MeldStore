"""Metadata-only before/after find comparison; fabricated rows are not payload evidence.

Run with uv and the same locked environment for both source trees. --baseline-src
must point to src extracted from commit 51bc084. No ambient database is used.
"""

import argparse
import json
import multiprocessing
import os
import sys
import time
from contextlib import contextmanager
from itertools import batched
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

RUNTIME_FIELDS = (
    'melddb_revision', 'python', 'sqlite_version', 'os', 'os_release',
    'architecture', 'logical_cpus', 'lock_xxh3_128', 'packages',
)

BASELINE_REVISION = '51bc084dcaf80db602bc870898ed7537c51a1735'
REQUIRED_MATRIX = {
    (adapter, count, clients)
    for adapter in ('sqlite', 'melddb', 'postgres')
    for count in (100, 1000)
    for clients in (1, 2, 4)
}


def source_bytes(source):
    """Read the selected package, rejecting files escaping its source root."""
    source = Path(source).resolve()
    files = {}
    for path in sorted((source / 'meldstore').rglob('*.py')):
        if not path.resolve().is_relative_to(source / 'meldstore'):
            raise ValueError('Package source escapes the selected directory')
        files[path.relative_to(source).as_posix()] = path.read_bytes()
    if not {'meldstore/__init__.py', 'meldstore/store.py', 'meldstore/encodings.py'} <= files.keys():
        raise ValueError('Selected package source is incomplete')
    return files


def fingerprints(files):
    import xxhash
    return {name: xxhash.xxh3_128(data).hexdigest() for name, data in files.items()}


def verify_baseline(source, *, revision=BASELINE_REVISION, repository=ROOT):
    """Match every Python file to one named Git tree, using one Git process."""
    import hashlib
    import subprocess

    repository = Path(repository).resolve()
    listing = subprocess.check_output([
        'git', '-c', f'safe.directory={repository.as_posix()}', '-c', 'core.bare=false',
        '-C', str(repository), 'ls-tree', '-r', '-z', revision, '--', 'src/meldstore',
    ])
    expected = {}
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        header, path = entry.split(b'\t', 1)
        name = path.decode('utf-8')
        if not name.endswith('.py'):
            continue
        mode, kind, oid = header.decode('ascii').split()
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Baseline tree contains unsupported Python source entries')
        expected[name.removeprefix('src/')] = oid
    files = source_bytes(source)
    if files.keys() != expected.keys():
        raise ValueError('Baseline Python file set differs from the named Git revision')
    normalized_files = []
    for name, data in files.items():
        header = f'blob {len(data)}\0'.encode()
        # Match Git object identity; this is not a payload integrity algorithm.
        algorithm = 'sha1' if len(expected[name]) == 40 else 'sha256'
        if hashlib.new(algorithm, header + data).hexdigest() != expected[name]:
            normalized = data.replace(b'\r\n', b'\n')
            normalized_header = f'blob {len(normalized)}\0'.encode()
            if hashlib.new(algorithm, normalized_header + normalized).hexdigest() != expected[name]:
                raise ValueError('Baseline Python contents differ from the named Git revision')
            normalized_files.append(name)
    return dict(verified=True, revision=revision, git_blobs=expected,
                crlf_normalized_files=normalized_files, sources=fingerprints(files))


def attest_source(source, expected=None):
    """Attest imported package locations and all Python bytes before timing."""
    import importlib

    source = Path(source).resolve()
    for name in ('meldstore', 'meldstore.store', 'meldstore.encodings'):
        importlib.import_module(name)
    files = source_bytes(source)
    imported = []
    for name, module in tuple(sys.modules.items()):
        if name == 'meldstore' or name.startswith('meldstore.'):
            path = Path(getattr(module, '__file__', '')).resolve()
            if not path.is_relative_to(source / 'meldstore'):
                raise ValueError('Imported MeldStore module is outside the selected source')
            if path.relative_to(source).as_posix() not in files:
                raise ValueError('Imported MeldStore module is outside the Python manifest')
            imported.append(name)
    sources = fingerprints(files)
    if expected is not None and sources != expected:
        raise ValueError('Selected source changed after the comparison manifest was recorded')
    return dict(imports_verified=True, imported_modules=sorted(imported), sources=sources)


def acceptance_profile(report):
    """Classify scale only; a full profile does not imply accepted results."""
    matrix = {(c['adapter'], c['records'], c['clients']) for c in report['comparisons']}
    return ('full' if REQUIRED_MATRIX <= matrix and report['samples'] >= 100
            and report['warmups'] >= 5 and report['relationships'] >= 300000 else 'diagnostic')


def validate_report(report):
    """Validate measurements without mutating raw evidence, including older reports.

    Returns an audit summary. An explicit source audit can cover missing worker
    attestations for historical baseline reads. Candidate attestations and runtime
    provenance are required for full acceptance. latency_memory_review
    remains 'pending' unless an explicit reviewer records 'approved' or 'rejected'.
    """
    import math

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def integer(value, minimum=0):
        return type(value) is int and value >= minimum

    require(integer(report.get('samples'), 1), 'Invalid sample count')
    require(integer(report.get('warmups')), 'Invalid warm-up count')
    require(integer(report.get('relationships')), 'Invalid relationship count')
    comparisons = report.get('comparisons')
    require(isinstance(comparisons, list), 'Missing comparisons')
    keys = []
    attested = {'baseline': True, 'candidate': True}
    baseline_sources = report.get('baseline_sources', {})
    candidate_sources = report.get('candidate_sources') or {
        name.removeprefix('src/'): value
        for name, value in report.get('provenance', {}).get('source_xxh3_128', {}).items()
        if name.startswith('src/meldstore/') and name.endswith('.py')
    }
    for comparison in comparisons:
        adapter, count, clients = (comparison.get(k) for k in ('adapter', 'records', 'clients'))
        require(adapter in {'sqlite', 'melddb', 'postgres'}, 'Invalid adapter')
        require(integer(count, 1) and count <= 1000, 'Invalid record count')
        require(integer(clients, 1) and clients <= 4, 'Invalid client count')
        keys.append((adapter, count, clients))
        per_revision = {}
        for label, sources in [('baseline', baseline_sources), ('candidate', candidate_sources)]:
            run = comparison.get(label, {})
            results = run.get('results', [])
            require(len(results) == clients, 'Missing worker results')
            sql_counts = []
            for result in results:
                require(result.get('status') == 'passed', 'Worker did not pass')
                require(result.get('returned_records') == count, 'Wrong returned cardinality')
                require(result.get('measured_reads') == report['samples'], 'Incomplete measured reads')
                require(result.get('warmups') == report['warmups'], 'Warm-up count differs')
                require(all(result.get(k) == 0 for k in ('errors', 'conflicts', 'storage_calls')),
                        'Unexpected read errors, conflicts or storage calls')
                require(integer(result.get('sql_per_find'), 1), 'Invalid SQL count')
                sql_counts.append(result['sql_per_find'])
                latency = result.get('latency', {})
                require(latency.get('samples') == report['samples']
                        and latency.get('unit') == 'seconds', 'Incomplete latency evidence')
                for key in ('p50', 'p95'):
                    value = latency.get(key)
                    require(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0,
                            'Invalid latency observation')
                require(latency['p50'] <= latency['p95'], 'Invalid latency percentiles')
                require(integer(result.get('peak_rss_bytes'), 1), 'Missing worker memory evidence')
                attestation = result.get('source_attestation')
                if attestation is None:
                    attested[label] = False
                else:
                    require(bool(sources) and attestation.get('sources') == sources,
                            'Worker source fingerprints differ from the report manifest')
                    require(attestation.get('imports_verified') is True,
                            'Worker source imports were not verified')
                    require({'meldstore', 'meldstore.store', 'meldstore.encodings'}
                            <= set(attestation.get('imported_modules', [])),
                            'Worker source imports are incomplete')
            require(len(set(sql_counts)) == 1, 'SQL counts differ between clients')
            require(integer(run.get('sampled_aggregate_rss_bytes'), 1),
                    'Missing aggregate memory evidence')
            per_revision[label] = sql_counts[0]
        reduction = 1 - per_revision['candidate'] / per_revision['baseline']
        require(math.isclose(comparison.get('sql_reduction', float('nan')), reduction,
                             rel_tol=1e-9, abs_tol=1e-12), 'Reported SQL reduction differs')
        if adapter == 'postgres' and count == 100:
            require(per_revision['candidate'] <= per_revision['baseline'] * .1,
                    'PostgreSQL SQL reduction is below 90%')
    require(len(keys) == len(set(keys)), 'Duplicate comparisons')
    requested = report.get('requested')
    complete_matrix = bool(keys)
    if requested is not None:
        declared = {(a, n, c) for a in requested['adapters']
                    for n in requested['counts'] for c in requested['clients']}
        complete_matrix = set(keys) == declared and bool(declared)
    completed = (complete_matrix and report.get('status') in {'passed', 'completed_measurements'}
                 and report.get('owned_services_removed') is True)
    review = report.get('latency_memory_review', 'pending')
    require(review in {'pending', 'approved', 'rejected'}, 'Invalid latency/memory review state')
    baseline_verified = report.get('baseline_verification', {})
    baseline_verified_by_workers = (
        report.get('baseline') == BASELINE_REVISION
        and baseline_verified.get('verified') is True
        and baseline_verified.get('revision') == BASELINE_REVISION
        and bool(baseline_sources) and baseline_verified.get('sources') == baseline_sources
        and attested['baseline']
    )
    audit = report.get('source_audit')
    if audit is not None:
        require(audit.get('status') == 'verified' and bool(audit.get('notes')),
                'Source audit requires verified status and explanatory notes')
        require(report.get('baseline') == BASELINE_REVISION
                and audit.get('baseline_revision') == BASELINE_REVISION
                and bool(baseline_sources) and bool(candidate_sources)
                and audit.get('baseline_sources') == baseline_sources
                and audit.get('candidate_sources') == candidate_sources,
                'Source audit does not match the recorded manifests')
    source_verified = bool(keys) and attested['candidate'] and (
        baseline_verified_by_workers or audit is not None
    )
    runtime = report.get('provenance') or {}
    runtime_verified = (all(runtime.get(key) for key in RUNTIME_FIELDS)
                        and bool(report.get('postgres_version'))
                        and bool(report.get('harness_xxh3_128')))
    profile = acceptance_profile(report)
    return dict(completed_measurements=completed, acceptance_profile=profile,
                worker_attestations_complete=all(attested.values()) and bool(keys),
                source_provenance_verified=source_verified, latency_memory_review=review,
                full_acceptance=completed and profile == 'full' and source_verified
                and runtime_verified and review == 'approved')



def reusable_baseline(report, sources, runtime, *, samples, warmups, relationships, matrix):
    """Reuse only complete baseline measurements with identical inputs/runtime."""
    import math

    if (report.get('baseline') != BASELINE_REVISION
            or report.get('baseline_sources') != sources
            or report.get('status') not in {'passed', 'completed_measurements'}
            or report.get('owned_services_removed') is not True):
        raise ValueError('Baseline report source or completion does not match')
    for key in RUNTIME_FIELDS:
        if key not in runtime or report.get('provenance', {}).get(key) != runtime[key]:
            raise ValueError('Baseline report runtime differs: ' + key)
    for name in ('tools/qualification/find_fixture.py', 'tools/qualification/fixture.py',
                 'tools/qualification/measurements.py', 'tools/qualification/concurrency.py',
                 'tools/qualification/services.py',
                 'tools/postgres_lab.py', 'tools/s3_lab.py', 'tools/qualify_local.py'):
        before = report.get('provenance', {}).get('source_xxh3_128', {}).get(name)
        after = runtime.get('source_xxh3_128', {}).get(name)
        if before is None or after is None:
            raise ValueError('Baseline fixture source evidence is missing')
        if before != after:
            audit = report.get('fixture_source_audit', {}).get(name, {})
            if (audit.get('recorded_xxh3_128') != before or audit.get('final_xxh3_128') != after
                    or (audit.get('ast_identical') is not True
                        and not audit.get('measurement_equivalence_review'))
                    or not audit.get('change')):
                raise ValueError('Baseline fixture changed without an explicit equivalence audit')
    if any(report.get(k) != v for k, v in dict(samples=samples, warmups=warmups,
                                               relationships=relationships).items()):
        raise ValueError('Baseline fixture or sample profile differs')
    results = {}
    for case in report.get('comparisons', []):
        key = case['adapter'], case['records'], case['clients']
        if key in results:
            raise ValueError('Duplicate baseline comparison')
        run = case['baseline']
        rows = run.get('results', [])
        if len(rows) != key[2] or run.get('sampled_aggregate_rss_bytes', 0) <= 0:
            raise ValueError('Baseline worker/memory evidence is incomplete')
        counts = set()
        for row in rows:
            if (row.get('status') != 'passed' or row.get('returned_records') != key[1]
                    or row.get('measured_reads') != samples or row.get('warmups') != warmups
                    or any(row.get(k) != 0 for k in ('errors', 'conflicts', 'storage_calls'))
                    or type(row.get('sql_per_find')) is not int or row['sql_per_find'] < 1
                    or row.get('peak_rss_bytes', 0) <= 0):
                raise ValueError('Baseline measurements are incomplete or failed')
            counts.add(row['sql_per_find'])
            timing = row.get('latency', {})
            if (timing.get('unit') != 'seconds' or timing.get('samples') != samples
                    or any(not isinstance(timing.get(k), (int, float))
                           or not math.isfinite(timing[k]) or timing[k] < 0
                           for k in ('p50', 'p95'))
                    or timing['p50'] > timing['p95']):
                raise ValueError('Baseline latency evidence is incomplete')
            attestation = row.get('source_attestation')
            if attestation is not None and (attestation.get('sources') != sources
                                            or attestation.get('imports_verified') is not True):
                raise ValueError('Baseline worker source differs')
        if len(counts) != 1:
            raise ValueError('Baseline SQL counts differ between workers')
        results[key] = run
    if set(results) != matrix:
        raise ValueError('Baseline comparison matrix differs')
    return results


@contextmanager
def catalog_for(config):
    from meldstore import Catalog
    catalog = (Catalog.connect_postgres(config['dsn']) if config['adapter'] == 'postgres'
               else Catalog(config['catalog'], adapter=config['adapter']))
    with catalog:
        yield catalog


def expected_records(schema, count):
    from tools.qualification.find_fixture import metadata_values, record_id
    return [dict(id=record_id(i), schema_name=schema.name, schema_version=1, state='ready',
                 token=f'token-{i:04d}', storage_id='synthetic-storage',
                 object_key=f'synthetic/{i:04d}', byte_size=0, digest='0' * 32,
                 hash_algorithm='xxh3_128', handler_id='file', handler_version=1, version=1,
                 metadata=schema.normalize_metadata(metadata_values(i))) for i in range(count)]


def worker(config, source, count, samples, warmups, barrier, lock, pipe):
    # Spawn starts without importing MeldStore. Both revisions share dependencies.
    sys.path.insert(0, source)
    from tools.qualification.find_fixture import find_schema, sql_only_store
    from tools.qualification.measurements import Measurements, latency
    from tools.qualify_local import peak_rss
    try:
        metrics = Measurements()
        schema = find_schema()
        expected = expected_records(schema, count)
        manager = catalog_for(config)
        with lock:
            catalog = manager.__enter__()
        try:
            store = sql_only_store(catalog)
            source_attestation = attest_source(source, config.get('expected_sources'))
            barrier.wait(timeout=120)
            with metrics.catalog(catalog), metrics.storage():
                observations, counts = [], []
                for n in range(warmups + samples):
                    before = metrics.calls.copy()
                    start = time.perf_counter()
                    rows = store.find(schema=schema, limit=count)
                    elapsed = time.perf_counter() - start
                    if rows != expected:
                        raise AssertionError('Find page differs from independent full-record oracle')
                    calls = dict(metrics.calls - before)
                    if any(k.startswith('storage.') for k in calls):
                        raise AssertionError('Find performed storage I/O')
                    if n >= warmups:
                        observations.append(elapsed)
                        counts.append(calls['sql'])
            if len(set(counts)) != 1:
                raise AssertionError('Read-only SQL count changed between samples')
            pipe.send(dict(status='passed', returned_records=len(rows), measured_reads=len(observations),
                           warmups=warmups, sql_per_find=counts[0], latency=latency(observations),
                           peak_rss_bytes=peak_rss(), storage_calls=0, errors=0, conflicts=0,
                           source_attestation=source_attestation))
        finally:
            manager.__exit__(None, None, None)
    except BaseException as exc:
        # Never expose DSNs or exception locals in public evidence.
        pipe.send(dict(status='failed', error_type=type(exc).__name__))
        raise
    finally:
        pipe.close()


def measure(config, source, count, clients, samples, warmups, seconds):
    from tools.qualification.concurrency import current_rss
    config = {**config, 'expected_sources': config.get('expected_sources')
              or fingerprints(source_bytes(source))}
    context = multiprocessing.get_context('spawn')
    barrier, lock = context.Barrier(clients), context.Lock()
    processes, readers = [], []
    deadline = time.monotonic() + seconds
    peak = 0
    try:
        for _ in range(clients):
            reader, writer = context.Pipe(duplex=False)
            process = context.Process(target=worker, args=(config, source, count, samples,
                                                          warmups, barrier, lock, writer))
            process.start()
            writer.close()
            processes.append(process)
            readers.append(reader)
        while any(p.is_alive() for p in processes):
            if time.monotonic() > deadline:
                raise TimeoutError('Find comparison time budget exhausted')
            memory = current_rss(os.getpid())
            for process in processes:
                if process.is_alive():
                    try:
                        memory += current_rss(process.pid)
                    except (OSError, FileNotFoundError):
                        pass  # A child can exit between is_alive and process sampling.
            peak = max(peak, memory)
            if memory > 4_000_000_000:
                raise RuntimeError('Aggregate memory budget exceeded')
            time.sleep(.05)
        results = [reader.recv() for reader in readers if reader.poll()]
        if (len(results) != clients or any(p.exitcode != 0 for p in processes)
                or any(r['status'] != 'passed' for r in results)):
            raise RuntimeError('Find worker failed')
        return dict(results=results, sampled_aggregate_rss_bytes=peak)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        for reader in readers:
            reader.close()


def setup(config, count, relationship_count):
    from meldstore.dialect import for_transaction
    from tools.qualification.find_fixture import record_id, seed_find
    from tools.qualification.fixture import DDL, relationships, verify_application
    from tools.qualification.measurements import Measurements
    manifest = dict(profile={'relationships': relationship_count}, files=[
        dict(id=record_id(i), config_id=f'config-{i % 3}', start_offset=i * 120)
        for i in range(count)])
    metrics = Measurements()
    with catalog_for(config) as catalog, metrics.catalog(catalog):
        seed_find(catalog, count=count, encoding='empty')
        with catalog.transaction() as tx:
            b = for_transaction(tx).bind
            for statement in DDL:
                tx.sql(statement)
            tx.sql_many(f'INSERT INTO app_configs VALUES({b},{b})',
                        [(f'config-{n}', f'configuration {n}') for n in range(3)])
            for chunk in batched(relationships(manifest), 1000):
                tx.sql_many('INSERT INTO app_intervals VALUES(' + ','.join([b] * 5) + ')', chunk)
        if not verify_application(catalog, manifest):
            raise AssertionError('Relationship fixture differs')
    return dict(metrics.calls)


def main():
    from tools.qualification.evidence import provenance, write_report
    from tools.qualification.resources import fresh_root
    from tools.qualification.services import Services
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-src', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reuse-baseline', type=Path,
                        help='Reuse completed baseline reads after source/runtime/profile checks')
    parser.add_argument('--samples', type=int, default=100)
    parser.add_argument('--warmups', type=int, default=5)
    parser.add_argument('--counts', type=int, nargs='+', default=[100, 1000])
    parser.add_argument('--clients', type=int, nargs='+', default=[1, 2, 4])
    parser.add_argument('--relationships', type=int, default=300000)
    parser.add_argument('--adapters', nargs='+', choices=['sqlite', 'melddb', 'postgres'],
                        default=['sqlite', 'melddb', 'postgres'])
    parser.add_argument('--seconds', type=float, default=10800)
    args = parser.parse_args()
    if (args.samples < 1 or args.warmups < 0 or args.relationships < 0
            or any(not 1 <= n <= 1000 for n in args.counts)
            or any(not 1 <= n <= 4 for n in args.clients)):
        parser.error('Invalid measurement bounds')
    baseline = args.baseline_src.resolve()
    if not (baseline / 'meldstore/store.py').is_file():
        parser.error('Baseline source is missing')
    baseline_verification = verify_baseline(baseline)
    candidate_sources = fingerprints(source_bytes(ROOT / 'src'))
    runtime = provenance()
    reused = None
    if args.reuse_baseline:
        prior = json.loads(args.reuse_baseline.read_text(encoding='utf-8'))
        reused = reusable_baseline(prior, baseline_verification['sources'], runtime,
                                   samples=args.samples, warmups=args.warmups,
                                   relationships=args.relationships,
                                   matrix={(a, n, c) for a in args.adapters
                                           for n in args.counts for c in args.clients})
    root, token = fresh_root(args.output, protected=[ROOT / 'src', ROOT / '.git'])
    report = dict(status='incomplete', scope='metadata-only, fabricated payload descriptors',
                  provenance=runtime, baseline=BASELINE_REVISION,
                  baseline_verification=baseline_verification, candidate_sources=candidate_sources,
                  requested=dict(adapters=args.adapters, counts=args.counts, clients=args.clients),
                  completed_measurements=False, acceptance_profile='diagnostic',
                  latency_memory_review='pending', full_acceptance=False,
                  samples=args.samples, warmups=args.warmups, relationships=args.relationships,
                  comparisons=[])
    import xxhash
    report['baseline_sources'] = baseline_verification['sources']
    report['harness_xxh3_128'] = xxhash.xxh3_128(Path(__file__).read_bytes()).hexdigest()
    if reused is not None:
        report['reused_baseline_report_xxh3_128'] = xxhash.xxh3_128(
            args.reuse_baseline.read_bytes()).hexdigest()
        report['reused_baseline_fixture_audit'] = prior.get('fixture_source_audit', {})
        report['source_audit'] = dict(
            status='verified', baseline_revision=BASELINE_REVISION,
            baseline_sources=report['baseline_sources'], candidate_sources=candidate_sources,
            notes='Baseline reads reused after Git source, complete profile and identical runtime '
                  'checks. Historical missing worker attestations remain missing. '
                  'Candidate source imports and bytes are attested by every new worker.')
    write_report(root / 'report.json', report)
    with Services(root, token) as services:
        if reused is not None and prior.get('postgres_version') != services.server_version:
            raise ValueError('Baseline PostgreSQL server version differs')
        report['postgres_version'] = services.server_version
        for adapter in args.adapters:
            for count in args.counts:
                with services.database() as dsn:
                    config = dict(adapter=adapter, dsn=dsn,
                                  catalog=str(root / f'{adapter}-{count}.db'))
                    print(f'Setup {adapter} records={count}', flush=True)
                    setup_calls = setup(config, count, args.relationships)
                    for clients in args.clients:
                        comparison = dict(adapter=adapter, records=count, clients=clients,
                                          setup_calls=setup_calls)
                        for label, source in [('baseline', baseline), ('candidate', ROOT / 'src')]:
                            if label == 'baseline' and reused is not None:
                                comparison[label] = reused[(adapter, count, clients)]
                                print(f'Reused verified baseline: {adapter}/{count}/{clients}', flush=True)
                                continue
                            print(f'Measure {adapter} records={count} clients={clients} {label}', flush=True)
                            manifest = (report['baseline_sources'] if label == 'baseline'
                                        else candidate_sources)
                            comparison[label] = measure({**config, 'expected_sources': manifest},
                                                        str(source), count, clients,
                                                        args.samples, args.warmups, args.seconds)
                        old = comparison['baseline']['results'][0]['sql_per_find']
                        new = comparison['candidate']['results'][0]['sql_per_find']
                        comparison['sql_reduction'] = 1 - new / old
                        if adapter == 'postgres' and count == 100 and new > old * .1:
                            raise AssertionError('PostgreSQL SQL reduction is below 90%')
                        report['comparisons'].append(comparison)
                        write_report(root / 'report.json', report)
                        print(f'Completed: SQL/find {old} -> {new}', flush=True)
    report['status'] = 'completed_measurements'
    report['owned_services_removed'] = True
    report.update(validate_report(report))
    write_report(root / 'report.json', report)
    print('Find comparison completed.', flush=True)


if __name__ == '__main__':
    main()
