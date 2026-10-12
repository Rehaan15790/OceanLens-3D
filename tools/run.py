#!/usr/bin/env python3
"""One test runner for OceanLens 3D. Every browser test runs against a built single file, never against src/.

    python tools/run.py                          all suites against the default build
    python tools/run.py --build dist/OceanLens3D.html --suite smoke,tour
    python tools/run.py --quick                  unit + server + smoke (what CI runs)
    python tools/run.py --suite fingerprint --fp-baseline tests/output/fingerprint-before.json
    python tools/run.py --list

Default build: $OCEANLENS_BUILD, then dist/OceanLens3D.html, then the root OceanLens3D.html.
Output goes to tests/output/ (gitignored): report.json, fingerprint.json, sizes/, downloads/.
"""
import argparse, hashlib, json, os, sys, time, traceback, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
sys.path.insert(0, str(REPO / 'tests'))

SUITES = {
    'unit':        ['test_pure', 'test_facts', '@pipeline'],
    'server':      ['test_server'],
    'smoke':       ['test_splash', 'test_smoke'],
    'extend':      ['test_extend'],
    'tour':        ['test_tour'],
    'robust':      ['test_robustness'],
    'security':    ['test_security_web'],
    'render':      ['test_render'],
    'a11y':        ['test_a11y'],
    'sizes':       ['test_sizes'],
    'perf':        ['test_perf'],
    'fingerprint': ['@fingerprint'],
}
QUICK = ['unit', 'server', 'smoke']
OUTPUT = REPO / 'tests' / 'output'


class Recorder(unittest.TestResult):
    """Collects one record per test (and per failing subtest) and prints a line as each finishes."""

    def __init__(self, suite):
        super().__init__()
        self.suite, self.records, self.t0 = suite, [], {}

    def startTest(self, test):
        super().startTest(test)
        self.t0[test.id()] = time.monotonic()

    def _add(self, test, status, err=None, sub=None):
        """Record one result and print its line, with the first lines of a failure's message."""
        tid = test.id() + (f' {sub._subDescription()}' if sub is not None else '')
        msg = ''
        if err is not None:
            etype, value, tb = err
            msg = str(value) if etype is AssertionError or etype.__name__ in ('AssertionError', 'SkipTest') else \
                ''.join(traceback.format_exception_only(etype, value)).strip()
        secs = time.monotonic() - self.t0.get(test.id(), time.monotonic())
        short = tid.split('.', 1)[-1] if '.' in tid else tid
        self.records.append({'suite': self.suite, 'test': short, 'status': status, 'message': msg, 'seconds': round(secs, 2)})
        tag = {'ok': 'ok  ', 'fail': 'FAIL', 'error': 'ERR ', 'skip': 'skip'}[status]
        print(f'  {tag} {short}' + (f'  ({secs:.1f} s)' if status == 'ok' and secs > 3 else ''))
        if msg and status != 'ok':
            lines = msg.strip().splitlines()
            for line in lines[:8]:
                print('         ' + line[:200])
            if len(lines) > 8:
                print(f'         ... {len(lines) - 8} more lines in tests/output/report.json')

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, 'ok')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, 'fail', err)

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, 'error', err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        status = 'xfail' if reason.startswith('pending: ') else 'skip'
        reason = reason[len('pending: '):] if status == 'xfail' else reason
        self.records.append({'suite': self.suite, 'test': test.id().split('.', 1)[-1], 'status': status,
                             'message': reason, 'seconds': 0})
        label = 'pend' if status == 'xfail' else 'skip'
        print(f'  {label} {test.id().split(".", 1)[-1]}\n         {reason[:240]}')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._add(test, 'fail' if issubclass(err[0], test.failureException) else 'error', err, sub=subtest)


def load(names, pattern):
    """Test module names (@pipeline: data-pipeline/test_*.py) -> one suite, kept to ids containing pattern."""
    loader, suite = unittest.TestLoader(), unittest.TestSuite()
    for n in names:
        if n == '@pipeline':
            pipe = REPO / 'data-pipeline'
            if any(pipe.glob('test_*.py')):
                suite.addTests(loader.discover(str(pipe), pattern='test_*.py', top_level_dir=str(pipe)))
            continue
        suite.addTests(loader.loadTestsFromName(n))
    if pattern:
        suite = unittest.TestSuite(t for t in _flatten(suite) if pattern in t.id())
    return suite


def _flatten(s):
    """Every test case in a nested suite."""
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


def run_fingerprint(build, baseline, save):
    """Evaluate tools/fingerprint.js in the built page; compare with a baseline fingerprint if one is given."""
    from cdp import Browser, open_app
    rec = []
    t0 = time.monotonic()
    with Browser() as b:
        open_app(b, build, enter=False)
        fp = b.eval((REPO / 'tools' / 'fingerprint.js').read_text(encoding='utf-8'), timeout=600)
        probs = [p['text'][:160] for p in b.problems() if 'ERR_NAME_NOT_RESOLVED' not in p['text']]
    save.parent.mkdir(parents=True, exist_ok=True)
    save.write_text(json.dumps(fp, indent=1, sort_keys=True), encoding='utf-8')
    status, msg = 'ok', f'{len(fp)} keys saved to {save.relative_to(REPO) if save.is_relative_to(REPO) else save}'
    if fp.get('error'):
        status, msg = 'error', 'fingerprint script threw: ' + fp['error'].splitlines()[0]
    elif probs:
        status, msg = 'fail', 'console problems while fingerprinting: ' + '; '.join(probs[:5])
    elif baseline:
        base = json.loads(Path(baseline).read_text(encoding='utf-8'))
        keys = sorted(set(base) | set(fp))
        changed = [k for k in keys if base.get(k) != fp.get(k)]
        if changed:
            status = 'fail'
            msg = f'{len(changed)} of {len(keys)} keys differ from {baseline}:\n' + '\n'.join(changed[:40])
        else:
            msg = f'identical to {baseline} ({len(keys)} keys)'
    secs = time.monotonic() - t0
    tag = {'ok': 'ok  ', 'fail': 'FAIL', 'error': 'ERR '}[status]
    print(f'  {tag} fingerprint  ({secs:.1f} s)')
    for line in msg.splitlines()[:12]:
        print('         ' + line)
    rec.append({'suite': 'fingerprint', 'test': 'fingerprint', 'status': status, 'message': msg, 'seconds': round(secs, 1)})
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split('\n', 2)[2])
    ap.add_argument('--build', help='built OceanLens3D.html to test')
    ap.add_argument('--suite', help='comma-separated: ' + ', '.join(SUITES))
    ap.add_argument('--quick', action='store_true', help='unit, server and smoke only')
    ap.add_argument('-k', dest='pattern', help='only tests whose id contains this text')
    ap.add_argument('--fp-baseline', help='fingerprint JSON to compare against')
    ap.add_argument('--fp-save', default=str(OUTPUT / 'fingerprint.json'), help='where to write the fingerprint')
    ap.add_argument('--list', action='store_true', help='list suites and tests, run nothing')
    args = ap.parse_args()
    # failure messages quote page text (e.g. U+2212 minus); redirected on Windows, stdout is cp1252 and a print would
    # raise inside the test result and end the run without a report
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')

    if args.build:
        os.environ['OCEANLENS_BUILD'] = str(Path(args.build).resolve())
    from cdp import CDPError, default_build, find_browsers
    suites = QUICK if args.quick else (args.suite.split(',') if args.suite else list(SUITES))
    unknown = [s for s in suites if s not in SUITES]
    if unknown:
        ap.error(f'unknown suite(s) {unknown}; choose from {list(SUITES)}')

    if args.list:
        for s in suites:
            print(s)
            for n in SUITES[s]:
                if n.startswith('@'):
                    print('   ', n[1:])
                    continue
                for t in _flatten(load([n], args.pattern)):
                    print('   ', t.id())
        return 0

    build = default_build()
    data = build.read_bytes()
    print(f'build    {build}  ({len(data) / 1e6:.2f} MB, sha256 {hashlib.sha256(data).hexdigest()[:12]})')
    try:
        print(f'browser  {" or ".join(find_browsers())}')
    except CDPError as e:
        print(f'browser  none: {e} (browser suites will error)')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records, t_all = [], time.monotonic()
    for s in suites:
        print(f'\n== {s}')
        if SUITES[s] == ['@fingerprint']:
            try:
                records += run_fingerprint(build, args.fp_baseline, Path(args.fp_save))
            except Exception as e:
                print(f'  ERR  fingerprint: {e}')
                records.append({'suite': s, 'test': 'fingerprint', 'status': 'error', 'message': str(e), 'seconds': 0})
            continue
        result = Recorder(s)
        load(SUITES[s], args.pattern).run(result)
        records += result.records

    kinds = ('ok', 'fail', 'error', 'xfail', 'skip')
    counts = {k: sum(1 for r in records if r['status'] == k) for k in kinds}
    print(f"\n{counts['ok']} passed, {counts['fail']} failed, {counts['error']} errors, {counts['xfail']} pending "
          f"(known gaps, scheduled), {counts['skip']} skipped in {time.monotonic() - t_all:.0f} s")
    by_suite = {}
    for r in records:
        by_suite.setdefault(r['suite'], dict.fromkeys(kinds, 0))[r['status']] += 1
    for s, c in by_suite.items():
        print(f"  {s:12s} " + '  '.join(f'{c[k]:3d} {k}' for k in kinds))
    (OUTPUT / 'report.json').write_text(json.dumps({'build': str(build), 'records': records}, indent=1), encoding='utf-8')
    return 1 if counts['fail'] or counts['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
