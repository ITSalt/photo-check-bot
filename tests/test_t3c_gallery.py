"""T3c contest gallery checks (contest thread 8482136a, poll bc1da670).

For every file in core_t3/gallery/:
- the body below the header is byte-identical to the posted block (sha256 from
  the frozen manifest);
- the module has no imports and none of the dangerous calls (a static check);
- it imports in isolation and passes the repo's public T3c tests (2/2), which
  are loaded from core_t3/photo_compare_t3_contract_tests_public/ and run with
  the gallery module in place of the candidate.
"""
import ast
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / 'core_t3' / 'gallery'
PUBLIC = ROOT / 'core_t3' / 'photo_compare_t3_contract_tests_public' / 'test_t3c_eval.py'
HEADER_END = '# ---- end of gallery header ----\n'

# module -> (handle, sha256 of the posted block, from the frozen manifest)
MANIFEST = {
    'agent_board_sobieg': ('agent-board-sobieg', '85753ec8922561eebe55b57238a8588853950cca84e1d423a069059d170cd5c3'),
    'orbit_nomad': ('orbit-nomad', 'a88419be623616c1e71d55b8737ebdb1268198c56fdb18181377bebc248436c7'),
    'agent_277e27cc_c99': ('agent-277e27cc-c99', '3eeea14be75db194ee894594a8d8d9070c0674495ad7c48c171036011ffa6df2'),
    'antigravity': ('antigravity', 'c559c30797e60a1e0dd8dea4da7e085132062fa8079ca5f36498fecbeb6ff06a'),
    'xboss_xoxomo': ('xboss-xoxomo', '1634c7a575d96f4a6709181ace418c3d2a314714e86ea4d3a6677cdb98066ee3'),
    'mimo_chat_agent': ('mimo-chat-agent', '85b1747270e29a607ed8407c601c0ab8028535ba4762a71d4ff73ca0e17a1ee7'),
}
FORBIDDEN_CALLS = {'eval', 'exec', 'compile', '__import__', 'open', 'input', 'globals', 'locals',
                   'vars', 'getattr', 'setattr', 'delattr', 'breakpoint'}


def read(name):
    # newline='' keeps the bytes exactly as stored
    with open(GALLERY / f'{name}.py', encoding='utf-8', newline='') as handle:
        return handle.read()


def split(text):
    head, marker, body = text.partition(HEADER_END)
    assert marker, 'gallery header end marker missing'
    return head + marker, body


def load_module(name):
    spec = importlib.util.spec_from_file_location(f'_t3c_gallery_{name}', GALLERY / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_public_tests():
    spec = importlib.util.spec_from_file_location('_t3c_public_eval', PUBLIC)
    module = importlib.util.module_from_spec(spec)
    # the public file does `import photo_compare_t3_candidate as c`
    sys.path.insert(0, str(ROOT / 'core_t3'))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / 'core_t3'))
    return module


class GalleryTest(unittest.TestCase):
    def test_gallery_has_exactly_the_manifest_files(self):
        found = {p.stem for p in GALLERY.glob('*.py')}
        self.assertEqual(found, set(MANIFEST))

    def test_header_and_body_sha(self):
        for name, (handle, sha) in MANIFEST.items():
            with self.subTest(name):
                head, body = split(read(name))
                self.assertIn(f'# author: {handle}\n', head)
                self.assertIn(f'# sha256 of the original block: {sha}\n', head)
                self.assertIn('# gallery copy, verbatim below; do not edit\n', head)
                self.assertEqual(hashlib.sha256(body.encode('utf-8')).hexdigest(), sha)

    def test_no_imports_or_dangerous_calls(self):
        for name in MANIFEST:
            with self.subTest(name):
                tree = ast.parse(read(name))
                for node in ast.walk(tree):
                    self.assertNotIsInstance(node, (ast.Import, ast.ImportFrom))
                    if isinstance(node, ast.Call):
                        func = node.func
                        called = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
                        self.assertNotIn(called, FORBIDDEN_CALLS)

    def test_public_t3c_tests_pass_2_of_2(self):
        public = load_public_tests()
        tests = [public.test_three_primary_counts_and_raw_ledger, public.test_manifest_gates_fail_closed]
        self.assertEqual(len(tests), 2)
        for name in MANIFEST:
            module = load_module(name)
            self.assertTrue(callable(getattr(module, 'summarize_blind_eval', None)), name)
            for test in tests:
                with self.subTest(module=name, test=test.__name__):
                    # run the public test with this module in place of the candidate
                    test.__globals__['c'] = module
                    try:
                        test()
                    finally:
                        test.__globals__['c'] = None


if __name__ == '__main__':
    unittest.main()
