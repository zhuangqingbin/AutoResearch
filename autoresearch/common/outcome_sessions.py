"""Pure calendar resolution. Sessions are supplied by the sourced calendar IO owner."""
import hashlib
import json
from datetime import date

LABEL_VERSION = 'outcome_labels.v2'


def compact(value):
    value = str(value or '').replace('-', '')
    try:
        if len(value) != 8 or not value.isdigit():
            return None
        date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        return value
    except ValueError:
        return None


def calendar_digest(sessions, quality):
    payload = {'quality': quality, 'sessions': sorted(set(sessions))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resolve_sessions(analysis_date, *, sessions, quality, today, horizons=(1, 2, 5, 10)):
    """Resolve exact future sessions; lack of long coverage never blocks overnight."""
    day, current = compact(analysis_date), compact(today)
    normalized = sorted({d for value in sessions for d in [compact(value)] if d})
    result = {'analysis_date': day or str(analysis_date), 'today': current or '',
              'sessions': normalized, 'calendar_quality': quality,
              'calendar_digest': calendar_digest(normalized, quality),
              **{f't{k}': None for k in set(horizons) | {1, 2, 5, 10}},
              'horizon_status': {}}
    after = [d for d in normalized if day and d > day]
    if day is None:
        status, reason = 'INVALID_ANALYSIS_DATE', 'Invalid analysis date'
    elif quality != 'trade_cal' or not normalized or current is None:
        status, reason = 'UNVERIFIED_CALENDAR', 'Calendar quality/coverage is unverified'
    elif day not in normalized and len(after) < 2:
        status, reason = 'UNVERIFIED_CALENDAR', 'Calendar coverage cannot establish analysis session'
    elif day not in normalized:
        status, reason = 'INVALID_ANALYSIS_DATE', 'Analysis date is not a supplied session'
    else:
        for k in set(horizons) | {1, 2, 5, 10}:
            target = after[k-1] if len(after) >= k else None
            result[f't{k}'] = target
            result['horizon_status'][k] = ('UNVERIFIED_CALENDAR' if target is None else
                'PENDING_SESSION' if target > current else 'OK')
        status = result['horizon_status'][2]
        reason = '' if status == 'OK' else ('Target session not reached' if status == 'PENDING_SESSION' else 'Calendar does not cover T+2')
    return {**result, 'status': status, 'reason': reason}
