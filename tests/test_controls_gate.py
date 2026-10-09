"""test_controls_gate.py v2 - SPDX-License-Identifier: MIT - author zenith-claude"""
import unittest
from app.controls_gate import ManifestError, validate_manifest, gate, format_report

LIC = 'https://example.org/licence'


def manifest(pos=20, neg=20):
    rows = [{'kind': 'same', 'ref': 'p%d.jpg' % i} for i in range(pos // 2)]
    rows += [{'kind': 'recrop', 'ref': 'p%d.jpg' % i, 'cand': 'c%d.jpg' % i}
             for i in range(pos - pos // 2)]
    rows += [{'kind': 'different', 'ref': 'a%d.jpg' % i, 'cand': 'b%d.jpg' % i,
              'licence': LIC} for i in range(neg)]
    return rows


def runs(match, pos_n, mismatch, neg_n, pos_other='NEEDS_REVIEW', neg_other='NEEDS_REVIEW'):
    out = [('same' if i % 2 else 'recrop', 'MATCH') for i in range(match)]
    out += [('same', pos_other)] * (pos_n - match)
    out += [('different', 'MISMATCH')] * mismatch
    out += [('different', neg_other)] * (neg_n - mismatch)
    return out


class Manifest(unittest.TestCase):
    def bad(self, rows, code):
        with self.assertRaises(ManifestError) as cm:
            validate_manifest(rows)
        msg = str(cm.exception)
        self.assertIn(code, msg)
        for leak in ('.jpg', 'https', 'example'):
            self.assertNotIn(leak, msg)

    def test_ok(self):
        self.assertIsNone(validate_manifest(manifest()))

    def test_1_too_few(self):
        self.bad(manifest(pos=19), 'TOO_FEW_POSITIVE')
        self.bad(manifest(neg=19), 'TOO_FEW_DIFFERENT')

    def test_5_different_rules(self):
        m = manifest(); del m[-1]['licence']
        self.bad(m, 'LICENCE_REQUIRED')
        m = manifest(); m[-1]['licence'] = 'http://example.org/l'
        self.bad(m, 'LICENCE_REQUIRED')
        m = manifest(); m[-1]['cand'] = m[-1]['ref']
        self.bad(m, 'DIFFERENT_SAME_FILE')

    def test_recrop_same_file(self):
        m = manifest(); m[10]['cand'] = m[10]['ref']
        self.bad(m, 'RECROP_SAME_FILE')

    def test_shape(self):
        self.bad('rows', 'NOT_A_LIST')
        self.bad(manifest() + [['kind', 'same']], 'NOT_A_DICT')
        m = manifest(); m[0]['note'] = 'x'
        self.bad(m, 'UNKNOWN_KEY')
        m = manifest(); m[0]['kind'] = 'similar'
        self.bad(m, 'UNKNOWN_KIND')
        m = manifest(); m[0]['ref'] = 7
        self.bad(m, 'NON_STRING_VALUE')
        m = manifest(); m[0]['cand'] = None
        self.bad(m, 'NON_STRING_VALUE')
        m = manifest(); del m[0]['ref']
        self.bad(m, 'REF_REQUIRED')
        m = manifest(); del m[10]['cand']
        self.bad(m, 'CAND_REQUIRED')

    def test_same_and_duplicates(self):
        m = manifest(); m[0]['cand'] = m[0]['ref']
        self.assertIsNone(validate_manifest(m))
        m = manifest(); m[0]['cand'] = 'other.jpg'
        self.bad(m, 'SAME_CAND_DIFFERS')
        m = manifest(); m.append(dict(m[-1]))
        self.bad(m, 'DUPLICATE')
        # "same" without cand and with cand == ref are one pair
        m = manifest(); m.append({'kind': 'same', 'ref': 'p0.jpg', 'cand': 'p0.jpg'})
        self.bad(m, 'DUPLICATE')


class Gate(unittest.TestCase):
    def test_2_boundary_passes(self):
        r = gate(runs(18, 20, 18, 20))
        self.assertTrue(r['pass'])
        self.assertEqual(r['failing'], [])
        self.assertEqual(r['positive'], {'n': 20, 'MATCH': 18, 'MISMATCH': 0,
                                         'NEEDS_REVIEW': 2, 'UNAVAILABLE': 0})
        self.assertEqual(r['negative']['MISMATCH'], 18)

    def test_3_positive_fails(self):
        r = gate(runs(17, 20, 20, 20))
        self.assertFalse(r['pass'])
        self.assertEqual(r['failing'], ['positive'])

    def test_4_unavailable_counts(self):
        r = gate(runs(18, 21, 18, 21, 'UNAVAILABLE', 'UNAVAILABLE'))
        self.assertEqual(r['positive']['n'], 21)
        self.assertEqual(r['positive']['UNAVAILABLE'], 3)
        self.assertEqual(r['failing'], ['positive', 'negative'])

    def test_false_match(self):
        r = gate(runs(20, 20, 19, 20, neg_other='MATCH'))
        self.assertFalse(r['pass'])
        self.assertEqual(r['failing'], ['false_match'])
        self.assertEqual(r['negative']['MATCH'], 1)
        r = gate(runs(20, 20, 17, 20, neg_other='MATCH'))
        self.assertEqual(r['failing'], ['negative', 'false_match'])
        r = gate(runs(17, 20, 39, 40, neg_other='MATCH'))
        self.assertEqual(r['failing'], ['positive', 'false_match'])
        # the other non-MISMATCH statuses stay inside the 90 percent bar
        for other in ('NEEDS_REVIEW', 'UNAVAILABLE'):
            self.assertTrue(gate(runs(20, 20, 18, 20, neg_other=other))['pass'])
        # a MATCH on the positive side is never a false match
        self.assertEqual(gate(runs(20, 20, 20, 20))['failing'], [])

    def test_6_bad_input(self):
        for item in (('same', 'OK'), ('same', None), ('twin', 'MATCH'),
                     ('same',), 'ab', 5, (['same'], 'MATCH')):
            with self.assertRaises(ValueError):
                gate([item])

    def test_small_n_fails_even_if_perfect(self):
        r = gate(runs(19, 19, 19, 19))
        self.assertEqual(r['failing'], ['positive', 'negative'])
        self.assertEqual(gate([])['failing'], ['positive', 'negative'])

    def test_iterable_and_repeats(self):
        r = gate(x for x in [('same', 'MATCH')] * 40 + [('different', 'MISMATCH')] * 40)
        self.assertTrue(r['pass'])
        self.assertEqual(r['positive']['n'], 40)

    def test_no_float_rounding_at_boundary(self):
        self.assertTrue(gate(runs(27, 30, 27, 30))['pass'])
        self.assertEqual(gate(runs(26, 29, 27, 30))['failing'], ['positive'])


class Report(unittest.TestCase):
    def test_lines(self):
        lines = format_report(gate(runs(18, 20, 18, 20))).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith('positive n=20: MATCH 18 (90.0%)'))
        self.assertIn('MISMATCH 18 (90.0%)', lines[1])
        self.assertEqual(lines[2], 'CONTROLS PASS')

    def test_fail_and_empty(self):
        lines = format_report(gate(runs(17, 20, 20, 20))).splitlines()
        self.assertEqual(lines[-1], 'CONTROLS FAIL: positive')
        lines = format_report(gate([])).splitlines()
        self.assertIn('n/a', lines[0])
        self.assertEqual(lines[-1], 'CONTROLS FAIL: positive, negative')
        lines = format_report(gate(runs(20, 20, 19, 20, neg_other='MATCH'))).splitlines()
        self.assertEqual(lines[-1], 'CONTROLS FAIL: false_match')

    def test_percent_floors(self):
        self.assertIn('MATCH 26 (89.6%)', format_report(gate(runs(26, 29, 27, 30))))


if __name__ == '__main__':
    unittest.main()
