#!/usr/bin/env python3
"""Everything CI checks, locally: repository hygiene, the build check, a fresh build into dist/, then the tests.

    python tools/check.py            all test suites
    python tools/check.py --quick    unit + server + smoke, as CI does
    python tools/check.py --no-build test the existing dist/ or root file instead of building

The fresh build goes to dist/platform.html.

Exit status is non-zero if any step fails. Nothing here writes the root OceanLens3D.html.
"""
import argparse, re, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

# absolute paths into someone's home folder must not be committed (docs use placeholders)
PERSONAL = re.compile(r'(?i)(?:[a-z]:[\\/]{1,2}users[\\/]{1,2}(?!public\b)[^\\/\s"\'<>]+|/home/[a-z][^/\s"\'<>]*/|/Users/(?!Public/|Shared/)[A-Za-z][^/\s"\'<>]*/)')
TEXT_SUFFIXES = {'.py', '.js', '.css', '.html', '.md', '.json', '.yml', '.yaml', '.txt', '.cfg', '.toml', ''}


def step(name, fn):
    """Run one step, print PASS or FAIL with its time; True when it passed."""
    t0 = time.monotonic()
    print(f'\n### {name}', flush=True)
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f'{type(e).__name__}: {e}'
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.monotonic() - t0:.0f} s){'  - ' + detail if detail else ''}", flush=True)
    return ok


def hygiene():
    """No home-folder paths in tracked files, and no LICENSE file until the owner chooses a licence."""
    files = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=REPO, capture_output=True, text=True, check=True).stdout.split('\n')
    found = []
    for f in filter(None, files):
        p = REPO / f
        if p.suffix.lower() not in TEXT_SUFFIXES or not p.exists() or p.stat().st_size > 8e6:
            continue
        for n, line in enumerate(p.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            m = PERSONAL.search(line)
            if m:
                found.append(f'{f}:{n}: {m.group(0)}')
    problems = [f'personal path {x}' for x in found[:20]]
    if any((REPO / n).exists() for n in ('LICENSE', 'LICENSE.md', 'LICENSE.txt')):
        problems.append('a LICENSE file exists, but the owner has not chosen a licence')
    for p in problems:
        print('  ' + p)
    return not problems, f'{len([f for f in files if f])} tracked or new files scanned'


def run(cmd):
    """Run a command in the project folder; (passed, detail)."""
    print('  $ ' + ' '.join(str(c) for c in cmd), flush=True)
    r = subprocess.run(cmd, cwd=REPO)
    return r.returncode == 0, f'exit {r.returncode}'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--quick', action='store_true', help='unit, server and smoke suites only (what CI runs)')
    ap.add_argument('--no-build', action='store_true', help='do not run build.py; test the existing build')
    ap.add_argument('--suite', help='passed through to tools/run.py')
    args = ap.parse_args()

    results = [step('repository hygiene', hygiene)]
    target = None
    if not args.no_build:
        results.append(step('build.py --check', lambda: run([PY, 'build.py', '--check'])))
        target = REPO / 'dist' / 'platform.html'
        results.append(step('build into dist/', lambda: run([PY, 'build.py', '--out', str(target)])))
    cmd = [PY, 'tools/run.py'] + (['--build', str(target)] if target else []) + \
          (['--quick'] if args.quick else []) + (['--suite', args.suite] if args.suite else [])
    results.append(step('tests', lambda: run(cmd)))
    print(f"\n{sum(results)} of {len(results)} steps passed")
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
