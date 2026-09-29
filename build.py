#!/usr/bin/env python3
"""Build OceanLens3D.html, the single self-contained file that ships, from src/.

    python build.py                           build to dist/OceanLens3D.html (use this while working)
    python build.py --out OceanLens3D.html    rebuild the shipped file at the project root
    python build.py --check                   check the sources and that the root file is up to date
    python build.py --watch                   rebuild dist/ whenever a source file changes
    python build.py --site dist/site          a static site with security headers (docs/DEPLOY.md)

src/index.html is the manifest. The build
  - puts every <link rel="stylesheet" href="css/..."> into one <style>, in order, with any
    url('../assets/...') replaced by a data: URI;
  - inlines every <script src="..."> as its own <script> ending in //# sourceURL=<path>, so DevTools
    and stack traces name the source file and line;
  - inlines <script type="application/json" id="..." src="....json"> as JSON, with every "<" written
    as \\u003c so nothing in the data can close the element;
  - stamps the VERSION file into <meta name="version">.
It stops with an error if any src, href or url() would point outside the file, if a file under src/css
or src/js is missing from the manifest or listed twice, if the data is not valid JSON, or if the result
is over the size budget. The same sources always give the same bytes. Python standard library only.
"""
import argparse
import base64
import hashlib
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
# External references allowed in the built page: none. The file must work with no network.
TEMPORARY_EXTERNAL = ()
BANNER = '<!-- Built by build.py from src/. Edit the sources, not this file. -->\n'
# the page's content security policy; script-src gets a hash for every inline script that runs
CSP = ("default-src 'none'; script-src {scripts}; style-src 'unsafe-inline'; img-src data: blob:; font-src 'none'; "
       "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")
SITE_HEADERS = ['Referrer-Policy: no-referrer', 'X-Content-Type-Options: nosniff',
                'Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()', 'Cache-Control: no-cache']
NOT_FOUND = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<title>Not found · OceanLens 3D</title>\n'
             '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n'
             '<body style="margin:0;padding:40px 24px;background:#0e1318;color:#e8ecef;font-family:system-ui,sans-serif">\n'
             '<h1 style="font-size:20px;font-weight:600">Page not found</h1>\n'
             '<p><a href="/" style="color:#e8ecef">Open OceanLens 3D</a></p>\n</body>\n</html>\n')

CSS_LINK = re.compile(r'<link rel="stylesheet" href="(css/[\w.-]+\.css)">\n')
JSON_TAG = re.compile(r'<script type="application/json" id="([\w-]+)" src="([\w./-]+\.json)"></script>')
SCRIPT_TAG = re.compile(r'<script src="([\w./-]+\.js)"></script>')
ASSET_URL = re.compile(r"url\('\.\./assets/([\w.-]+)'\)")
MIME = {'.png': 'image/png', '.svg': 'image/svg+xml', '.jpg': 'image/jpeg', '.webp': 'image/webp'}


class BuildError(Exception):
    pass


def read_text(path):
    # line endings are normalised the way the HTML parser does, so script hashes match what it sees
    with open(path, encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, '/')


def src_path(ref):
    """A path written in src/index.html (relative to src/) as an absolute path; it must exist."""
    path = os.path.normpath(os.path.join(SRC, ref))
    if not os.path.isfile(path):
        raise BuildError(f'src/index.html refers to {ref}, which does not exist')
    return path


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
        if key == 'vendor':
            # MIT and similar licences require the notice in every copy: vendor/<name>.LICENSE goes on top
            lic = os.path.join(os.path.dirname(path), os.path.basename(path).split('.')[0] + '.LICENSE')
            if os.path.isfile(lic):
                text = read_text(lic).strip()
                if '*/' in text:
                    raise BuildError(f'{rel(lic)} contains "*/" and cannot be put in a comment')
                code = '/*\n' + text + '\n*/\n' + code
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
            if not u.startswith(('#', 'data:')) and not (TEMPORARY_EXTERNAL and u.startswith(TEMPORARY_EXTERNAL))]
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

    # the policy blocks inline event handlers and javascript: URLs, so the build refuses them
    handlers = sorted(set(re.findall(r'<[a-z][^>]*?\s(on[a-z]+)\s*=', tags, re.I)))
    if handlers or re.search(r'javascript:', tags, re.I):
        raise BuildError('inline event handlers or javascript: URLs in src/index.html: ' + ', '.join(handlers or ['javascript:']))
    hashes = []
    for m in re.finditer(r'<!--.*?-->|<script\b([^>]*)>(.*?)</script>', page, re.S | re.I):
        if m.group(0).startswith('<!--'):
            continue            # a comment can mention <script> without opening one
        attrs, body = m.group(1), m.group(2)
        if 'src=' in attrs or ('type=' in attrs and 'javascript' not in attrs and 'module' not in attrs):
            continue
        hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(body.encode('utf-8')).digest()).decode('ascii') + "'")
    csp = CSP.format(scripts=' '.join(hashes))
    marker = f'<meta name="version" content="{version}">\n'
    page = page.replace(marker, marker + f'<meta http-equiv="Content-Security-Policy" content="{csp}">\n', 1)

    data = page.encode('utf-8')
    if len(data) > BUDGET:
        raise BuildError(f'{len(data):,} bytes is over the {BUDGET:,} byte budget')
    return data, sizes, version, csp


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
        data, _, _, _ = build()
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
    data, sizes, version, _ = build()
    write(data, out)
    parts = ', '.join(f'{k} {v / 1e3:.0f} kB' for k, v in sizes.items())
    print(f'built {rel(out)} {version}: {len(data) / 1e6:.2f} MB ({parts})')


def build_site(folder):
    """A static site for a host that reads a _headers file (Cloudflare Pages, Netlify)."""
    data, _, version, csp = build()
    write(data, os.path.join(folder, 'index.html'))
    write(NOT_FOUND.encode('utf-8'), os.path.join(folder, '404.html'))
    write(b'User-agent: *\nDisallow:\n', os.path.join(folder, 'robots.txt'))
    headers = ['/*', f"  Content-Security-Policy: {csp}; frame-ancestors 'none'"] + ['  ' + h for h in SITE_HEADERS]
    write(('\n'.join(headers) + '\n').encode('utf-8'), os.path.join(folder, '_headers'))
    print(f'built site {rel(folder)} {version}: index.html, 404.html, robots.txt, _headers')


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
    ap.add_argument('--site', metavar='DIR', help='write a static site: index.html, 404.html, robots.txt, _headers')
    args = ap.parse_args()
    if args.check:
        return check()
    if args.site:
        try:
            build_site(os.path.abspath(args.site))
        except BuildError as e:
            print('error: ' + str(e))
            return 1
        return 0
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
