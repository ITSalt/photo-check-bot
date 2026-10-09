"""controls_gate.py v2 - SPDX-License-Identifier: MIT - author zenith-claude
Controls gate for a photo-comparison checker. Standard library only."""
KEYS = ('kind', 'ref', 'cand', 'licence')
POSITIVE = ('same', 'recrop')
STATUSES = ('MATCH', 'MISMATCH', 'NEEDS_REVIEW', 'UNAVAILABLE')
MIN_ROWS = 20
NL = chr(10)


class ManifestError(ValueError):
    """Message holds a row index and a fixed code, never a path or a URL."""


def _bad(i, code):
    raise ManifestError('row %d: %s' % (i, code))


def validate_manifest(rows):
    if not isinstance(rows, list):
        raise ManifestError('manifest: NOT_A_LIST')
    seen, pos, neg = set(), 0, 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            _bad(i, 'NOT_A_DICT')
        for k, v in row.items():
            if k not in KEYS:
                _bad(i, 'UNKNOWN_KEY')
            if not isinstance(v, str):
                _bad(i, 'NON_STRING_VALUE')
        kind, ref = row.get('kind'), row.get('ref')
        if kind not in POSITIVE and kind != 'different':
            _bad(i, 'UNKNOWN_KIND')
        if not ref:
            _bad(i, 'REF_REQUIRED')
        cand = row.get('cand')
        if kind == 'same':
            # a "same" row is one photo against itself; another file is a recrop
            if cand is None:
                cand = ref
            if cand != ref:
                _bad(i, 'SAME_CAND_DIFFERS')
        elif not cand:
            _bad(i, 'CAND_REQUIRED')
        elif kind == 'recrop' and cand == ref:
            # a self-pair filed as a recrop would pad the positive side
            _bad(i, 'RECROP_SAME_FILE')
        if kind == 'different':
            if cand == ref:
                _bad(i, 'DIFFERENT_SAME_FILE')
            lic = row.get('licence')
            if not lic or not lic.startswith('https://') or len(lic) <= 8:
                _bad(i, 'LICENCE_REQUIRED')
            neg += 1
        else:
            pos += 1
        key = (kind, ref, cand)
        if key in seen:
            _bad(i, 'DUPLICATE')
        seen.add(key)
    if pos < MIN_ROWS:
        raise ManifestError('manifest: TOO_FEW_POSITIVE')
    if neg < MIN_ROWS:
        raise ManifestError('manifest: TOO_FEW_DIFFERENT')


def gate(outcomes):
    sides = {s: dict.fromkeys(('n',) + STATUSES, 0) for s in ('positive', 'negative')}
    for item in outcomes:
        try:
            kind, status = item
        except (TypeError, ValueError):
            raise ValueError('outcome is not a (kind, status) pair') from None
        if isinstance(kind, str) and kind in POSITIVE:
            side = sides['positive']
        elif isinstance(kind, str) and kind == 'different':
            side = sides['negative']
        else:
            raise ValueError('unknown kind')
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError('unknown status')
        side['n'] += 1
        side[status] += 1
    failing = []
    for name, want in (('positive', 'MATCH'), ('negative', 'MISMATCH')):
        s = sides[name]
        if not (s['n'] >= MIN_ROWS and s[want] * 10 >= 9 * s['n']):
            failing.append(name)
    if sides['negative']['MATCH'] > 0:
        # a false MATCH is the one error the checker exists to prevent
        failing.append('false_match')
    return {'positive': sides['positive'], 'negative': sides['negative'],
            'pass': not failing, 'failing': failing}


def _pct(count, n):
    if n == 0:
        return 'n/a'
    p = count * 1000 // n  # floor: 89.99 prints as 89.9, never as 90.0
    return '%d.%d%%' % (p // 10, p % 10)


def format_report(result):
    lines = []
    for name in ('positive', 'negative'):
        s = result[name]
        parts = ['%s %d (%s)' % (st, s[st], _pct(s[st], s['n'])) for st in STATUSES]
        lines.append('%s n=%d: %s' % (name, s['n'], ', '.join(parts)))
    if result['pass']:
        lines.append('CONTROLS PASS')
    else:
        lines.append('CONTROLS FAIL: ' + ', '.join(result['failing']))
    return NL.join(lines)
