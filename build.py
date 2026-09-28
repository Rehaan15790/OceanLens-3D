#!/usr/bin/env python3
"""Build OceanLens3D.html, the single self-contained file that ships, from src/.

    python build.py                           build to dist/OceanLens3D.html (use this while working)
    python build.py --out OceanLens3D.html    rebuild the shipped file at the project root
    python build.py --check                   check the sources and that the root file is up to date
    python build.py --watch                   rebuild dist/ whenever a source file changes

src/index.html is the manifest. The build
  - puts every <link rel="stylesheet" href="css/..."> into one <style>, in order, with any
    url('../assets/...') replaced by a data: URI;
  - inlines every <script src="..."> as its own <script> ending in //# sourceURL=<path>, so DevTools
    and stack traces name the source file and line;
  - inlines <script type="application/json" id="..." src="....json"> as JSON, with every "<" written
    as \\u003c so nothing in the data can close the element;
  - stamps the VERSION file into <meta name="version">.
It stops with an error if a src or href would still point outside the file, if a file under src/css
or src/js is missing from the manifest or listed twice, if the data is not valid JSON, or if the result
is over the size budget. The same sources always give the same bytes. Python standard library only.
"""
import argparse
import base64
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'src')
SHIPPED = os.path.join(ROOT, 'OceanLens3D.html')
DEFAULT_OUT = os.path.join(ROOT, 'dist', 'OceanLens3D.html')
BUDGET = 3_500_000                  # bytes; the pitch promises "about 3 MB"
# The only external reference allowed, until the web-font link is removed from src/index.html.
TEMPORARY_EXTERNAL = ('https://fonts.googleapis.com',)
BANNER = '<!-- Built by build.py from src/. Edit the sources, not this file. -->\n'

CSS_LINK = re.compile(r'<link rel="stylesheet" href="(css/[\w.-]+\.css)">\n')
JSON_TAG = re.compile(r'<script type="application/json" id="([\w-]+)" src="([\w./-]+\.json)"></script>')
SCRIPT_TAG = re.compile(r'<script src="([\w./-]+\.js)"></script>')
ASSET_URL = re.compile(r"url\('\.\./assets/([\w.-]+)'\)")
MIME = {'.png': 'image/png', '.svg': 'image/svg+xml', '.jpg': 'image/jpeg', '.webp': 'image/webp'}


class BuildError(Exception):
    pass


def read_text(path):
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def src_path(ref):
    """A path written in src/index.html (relative to src/) as an absolute path."""
    return os.path.normpath(os.path.join(SRC, ref))


def inline_assets(css, css_file):
    def data_uri(m):
        path = os.path.join(SRC, 'assets', m.group(1))
        mime = MIME.get(os.path.splitext(path)[1].lower())
        if not mime:
            raise BuildError(f'{rel(css_file)}: unsupported asset type {m.group(1)}')
        with open(path, 'rb') as f:
            return f"url('data:{mime};base64," + base64.b64encode(f.read()).decode('ascii') + "')"
    return ASSET_URL.sub(data_uri, css)


def build():
    """Return the built page as bytes and a dict of sizes per part."""
    page = read_text(os.path.join(SRC, 'index.html'))
    sizes = {}
    listed = []

    # stylesheets -> one <style> where the first link was
    links = CSS_LINK.findall(page)
    css = []
    for ref in links:
        path = src_path(ref)
        listed.append(path)
        css.append(inline_assets(read_text(path), path))
    css = ''.join(css)
    sizes['css'] = len(css.encode('utf-8'))
    first = [True]

    def style_tag(_):
        if first[0]:
            first[0] = False
            return '<style>\n' + css + '</style>\n'
        return ''
    page = CSS_LINK.sub(style_tag, page)

    # data blocks
    def json_tag(m):
        path = src_path(m.group(2))
        text = read_text(path).strip()
        try:
            json.loads(text)
        except ValueError as e:
            raise BuildError(f'{rel(path)} is not valid JSON: {e}')
        sizes['data'] = sizes.get('data', 0) + len(text.encode('utf-8'))
        return f'<script type="application/json" id="{m.group(1)}">' + text.replace('<', '\\u003c') + '</script>'
    page = JSON_TAG.sub(json_tag, page)

    # scripts, one element each
    def script_tag(m):
        path = src_path(m.group(1))
        listed.append(path)
        code = read_text(path)
        if not code.endswith('\n'):
            code += '\n'
        if '</script' in code.lower():
            raise BuildError(f'{rel(path)} contains "</script", which would end the inlined element')
        key = 'vendor' if rel(path).startswith('vendor/') else 'js'
        sizes[key] = sizes.get(key, 0) + len(code.encode('utf-8'))
        return f'<script>\n{code}//# sourceURL={rel(path)}\n</script>'
    page = SCRIPT_TAG.sub(script_tag, page)

    # every stylesheet and script under src/ is in the manifest exactly once
    for sub, ext in (('css', '.css'), ('js', '.js')):
        for name in sorted(os.listdir(os.path.join(SRC, sub))):
            if name.endswith(ext):
                n = listed.count(os.path.join(SRC, sub, name))
                if n != 1:
                    raise BuildError(f'src/{sub}/{name} is listed {n} times in src/index.html (expected once)')

    # nothing may point outside the file (attributes are checked with script and style bodies left out)
    tags = re.sub(r'(<(script|style)\b[^>]*>).*?(</\2>)', r'\1\3', page, flags=re.S | re.I)
    left = [u for u in re.findall(r'\b(?:src|href)\s*=\s*["\']([^"\']+)["\']', tags)
            if not u.startswith('#') and not u.startswith(TEMPORARY_EXTERNAL)]
    left += [u for u in re.findall(r'url\(\s*["\']?([^"\')]+)', css) if not u.startswith('data:')]
    if left:
        raise BuildError('references left unresolved: ' + ', '.join(sorted(set(left))))

    version_file = os.path.join(ROOT, 'VERSION')
    version = read_text(version_file).strip() if os.path.exists(version_file) else 'unversioned'
    if not re.fullmatch(r'[\w.+-]+', version):
        raise BuildError(f'VERSION must be one word, got {version!r}')
    head = '<meta charset="UTF-8">\n'
    if head not in page:
        raise BuildError(f'src/index.html has no {head.strip()!r} line to put the version after')
    page = page.replace(head, head + f'<meta name="version" content="{version}">\n', 1)
    if not page.startswith('<!DOCTYPE html>\n'):
        raise BuildError('src/index.html must start with <!DOCTYPE html>')
    page = page.replace('<!DOCTYPE html>\n', '<!DOCTYPE html>\n' + BANNER, 1)

    data = page.encode('utf-8')
    if len(data) > BUDGET:
        raise BuildError(f'{len(data):,} bytes is over the {BUDGET:,} byte budget')
    return data, sizes, version


def write(data, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, out)


def material_warnings():
    """Plan decision m: materials are made in js/render.js only."""
    found = []
    js = os.path.join(SRC, 'js')
    for name in sorted(os.listdir(js)):
        if name.endswith('.js') and name != 'render.js':
            n = len(re.findall(r'new THREE\.\w*Material\(', read_text(os.path.join(js, name))))
            if n:
                found.append(f'{name} ({n})')
    return found


def check():
    problems = []
    try:
        data, _, _ = build()
    except BuildError as e:
        problems.append(str(e))
        data = None
    if data is not None:
        if not os.path.exists(SHIPPED):
            problems.append('OceanLens3D.html is missing: run python build.py --out OceanLens3D.html')
        else:
            with open(SHIPPED, 'rb') as f:
                if f.read() != data:
                    problems.append('OceanLens3D.html is stale: run python build.py --out OceanLens3D.html')
    mats = material_warnings()
    if mats:
        print('warning: THREE materials created outside js/render.js: ' + ', '.join(mats))
    for p in problems:
        print('error: ' + p)
    if not problems:
        print('build check: ok')
    return 1 if problems else 0


def build_once(out):
    data, sizes, version = build()
    write(data, out)
    parts = ', '.join(f'{k} {v / 1e3:.0f} kB' for k, v in sizes.items())
    print(f'built {rel(out)} {version}: {len(data) / 1e6:.2f} MB ({parts})')


def watch(out):
    watched = [SRC, os.path.join(ROOT, 'vendor'), os.path.join(ROOT, 'data')]

    def stamp():
        times = [os.path.getmtime(os.path.join(d, f)) for base in watched if os.path.isdir(base)
                 for d, _, files in os.walk(base) for f in files]
        version_file = os.path.join(ROOT, 'VERSION')
        if os.path.exists(version_file):
            times.append(os.path.getmtime(version_file))
        return max(times)
    last = None
    print('watching src/, vendor/, data/ and VERSION; Ctrl+C to stop')
    while True:
        now = stamp()
        if now != last:
            last = now
            try:
                build_once(out)
            except BuildError as e:
                print('error: ' + str(e))
        time.sleep(0.4)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=DEFAULT_OUT, help='output file (default: dist/OceanLens3D.html)')
    ap.add_argument('--check', action='store_true', help='check the sources and the shipped file; write nothing')
    ap.add_argument('--watch', action='store_true', help='rebuild whenever a source changes')
    args = ap.parse_args()
    if args.check:
        return check()
    out = os.path.abspath(args.out)
    if args.watch:
        watch(out)
        return 0
    try:
        build_once(out)
    except BuildError as e:
        print('error: ' + str(e))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
