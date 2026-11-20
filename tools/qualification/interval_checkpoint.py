"""Validated interval checkpoints; source reports are read-only, never databases."""

import copy
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import xxhash

from tools.qualification.evidence import sanitized
from tools.qualification.interval_evidence import require, validate


def utc_now():
    return datetime.now(UTC).isoformat()


def digest(cell):
    return xxhash.xxh3_128(json.dumps(cell, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def timestamp(value):
    stamp = datetime.fromisoformat(value)
    require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0,
            'Session timestamps must be UTC')
    return stamp


def validate_sessions(report, *, closed):
    require(report.get('continuity_scope') == 'within-sessions', 'Continuity scope')
    sessions = report.get('sessions', [])
    require(bool(sessions), 'Missing sessions')
    require(len(report.get('checkpoints', [])) == len(report['cells']), 'Checkpoint count')
    cursor, ids, previous_end = 0, set(), None
    for number, session in enumerate(sessions):
        identifier = session['id']
        require(isinstance(identifier, str) and re.fullmatch('[0-9a-f]{32}', identifier)
                and identifier not in ids, 'Session ID')
        ids.add(identifier)
        start, end = session['start_cell'], session['end_cell']
        require(type(start) is int and type(end) is int and start == cursor
                and start <= end <= len(report['cells']), 'Session partition')
        began = timestamp(session['started_at'])
        require(previous_end is None or began >= previous_end, 'Session chronology')
        stamp = timestamp(session['checkpoint_at'])
        require(stamp >= began, 'Checkpoint chronology')
        if closed or number < len(sessions) - 1:
            require(session['cleanup'] is True and session['continuity'] is True,
                    'Session cleanup/continuity')
            previous_end = timestamp(session['ended_at'])
            require(previous_end >= stamp, 'Session end')
        require(session['provenance'] == report['provenance'], 'Session provenance')
        limits = session['limits']
        require(limits == report['limits'], 'Session fixed budgets')
        require(set(limits) == {'memory_bytes', 'disk_bytes', 'reserve_bytes'}
                and all(type(v) is int and v >= (0 if k == 'reserve_bytes' else 1)
                        for k, v in limits.items()), 'Session limits')
        require(type(session['seconds']) in (int, float) and math.isfinite(session['seconds'])
                and session['seconds'] > 0, 'Session time budget')
        execution = session['execution']
        require(session['continuity'] is True and execution.get('continuity_lost') is False
                and execution.get('gap_limit_seconds') == 30
                and type(execution.get('maximum_scheduling_gap_seconds')) in (float, int)
                and 0 <= execution['maximum_scheduling_gap_seconds'] <= 30, 'Session execution')
        require(session.get('service') == report.get('service'), 'Session service changed')
        for ordinal in range(start, end):
            cell = report['cells'][ordinal]
            require(cell.get('session_id') == identifier, 'Cell session')
            require(report['checkpoints'][ordinal] ==
                    {'ordinal': ordinal, 'session_id': identifier, 'xxh3_128': digest(cell)},
                    'Checkpoint checksum')
        cursor = end
    require(cursor == len(report['cells']), 'Session coverage')


def load_checkpoint(path, current_provenance):
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    require(report.get('schema_version') == 2, 'Resume requires a v2 checkpoint, not legacy evidence')
    require(report.get('status') in ('paused', 'failed') and report.get('resume_eligible') is True,
            'Resume requires a stopped, eligible checkpoint')
    require(report.get('cleanup') is True and report.get('continuity') is True,
            'Resume requires successful cleanup and continuity')
    require(report['provenance'] == current_provenance, 'Resume provenance differs')
    require(report.get('full_metadata_qualified') is False
            and 0 < len(report['cells']) < len(report['schedule']), 'No unfinished checkpoint prefix')
    validate(report, partial=True)
    validate_sessions(report, closed=True)
    return copy.deepcopy(report)


def checkpoint(report, result, guard, current_provenance):
    require(current_provenance == report['provenance'], 'Checkpoint provenance changed')
    guard.check()
    candidate = copy.deepcopy(report)
    session = candidate['sessions'][-1]
    cell = {**result, 'session_id': session['id']}
    candidate['checkpoints'].append({'ordinal': len(candidate['cells']),
                                    'session_id': session['id'], 'xxh3_128': digest(cell)})
    candidate['cells'].append(cell)
    candidate['provenance_after'] = current_provenance
    session.update(end_cell=len(candidate['cells']), checkpoint_at=utc_now(),
                   continuity=True, execution=guard.evidence())
    validate(candidate, partial=True)
    return candidate


def session_summary(report):
    sessions = report['sessions']
    lines = ['', '## Measurement sessions', '',
             f'{len(sessions)} session(s); continuity was checked within each session.',
             'Pauses can change host load and OS cache state. Cross-session ratios are observations,',
             'not uninterrupted paired measurements. Check raw samples before choosing a layout.', '',
             '| Session | Treatments (1-based) | Started UTC | Ended UTC |',
             '| --- | --- | --- | --- |']
    for session in sessions:
        lines.append(f"| {session['id']} | {session['start_cell'] + 1}-{session['end_cell']} | "
                     f"{session['started_at']} | {session['ended_at']} |")
    baselines = {(c['adapter'], c['treatment'], c['round']): c['session_id']
                 for c in report['cells'] if c['layout'] == 'start'}
    cross = [c for c in report['cells'] if c['session_id'] !=
             baselines[c['adapter'], c['treatment'], c['round']]]
    lines += ['', 'Treatments whose start-only baseline was measured in another session:']
    lines += ([f"- {c['adapter']} / {c['treatment']} / {c['layout']} / round {c['round']}"
               for c in cross] or ['None.'])
    sanitized(lines)
    return lines
