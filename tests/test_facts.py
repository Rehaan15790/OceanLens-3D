"""Every number the UI states comes from the runtime `facts` module; this checks each one against an independent
Python recomputation by science (data-pipeline/facts_reference.py).

tests/facts_expected.json maps a dotted path under window.__ol.facts to its expected value, for example
    "real.path.heatBefore": {"value": 85.1, "tol": 0.05, ...}
    "real.comparison.worst[5].id": {"value": "ARGO-2902769"}
Numbers compare within `tol`; everything else compares exactly. The test skips until the build exposes
window.__ol.facts and the expected file exists.
"""
import json
import re
import unittest

from helpers import REPO, AppTestCase

EXPECTED = REPO / 'tests' / 'facts_expected.json'
MISSING = object()


def resolve(obj, path):
    for name, index in re.findall(r'([^.\[\]]+)|\[(\d+)\]', path):
        if name:
            if not isinstance(obj, dict) or name not in obj:
                return MISSING
            obj = obj[name]
        else:
            i = int(index)
            if not isinstance(obj, list) or i >= len(obj):
                return MISSING
            obj = obj[i]
    return obj


class Facts(AppTestCase):
    enter = False

    def setUp(self):
        super().setUp()
        if not self.js('!!(window.__ol && __ol.facts)'):
            self.skipTest('this build does not expose window.__ol.facts yet')
        if not EXPECTED.exists():
            self.skipTest('tests/facts_expected.json does not exist yet')
        self.expected = json.loads(EXPECTED.read_text(encoding='utf-8'))
        self.facts = self.js("JSON.parse(JSON.stringify(typeof __ol.facts === 'function' ? __ol.facts() : __ol.facts))")

    def test_facts_match_the_independent_recomputation(self):
        missing, wrong = [], []
        for path, spec in self.expected.items():
            got, want = resolve(self.facts, path), spec['value']
            if got is MISSING:
                missing.append(path)
            elif isinstance(want, (int, float)) and not isinstance(want, bool):
                if not isinstance(got, (int, float)) or abs(got - want) > spec.get('tol', 0):
                    wrong.append(f"{path}: app {got}, recomputed {want} (tol {spec.get('tol', 0)})")
            elif got != want:
                wrong.append(f'{path}: app {got!r}, recomputed {want!r}')
        self.assertFalse(missing or wrong, f'{len(missing)} facts missing, {len(wrong)} differ '
                         f'(of {len(self.expected)}):\n' + '\n'.join(wrong[:30] + [f'missing: {p}' for p in missing[:20]]))


if __name__ == '__main__':
    unittest.main()
