#!/usr/bin/env python3
"""Build OceanLens3D.html, the single self-contained file that ships, from src/.

    python build.py                           build to dist/OceanLens3D.html (use this while working)
    python build.py --out OceanLens3D.html    rebuild the shipped file at the project root
    python build.py --check                   check the sources and that the root file is up to date
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

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'src')
SHIPPED = os.path.join(ROOT, 'OceanLens3D.html')
DEFAULT_OUT = os.path.join(ROOT, 'dist', 'OceanLens3D.html')
BUDGET = 3_500_000                  # bytes; the pitch promises "about 3 MB"
BANNER = '<!-- Built by build.py from src/. Edit the sources, not this file. -->\n'
# the page's content security policy; script-src and style-src get a hash for every inline script and style that runs,
# so neither allows 'unsafe-inline' (nothing here uses eval, inline handlers, javascript: URLs or style="" markup)
CSP = ("default-src 'none'; script-src {scripts}; style-src {styles}; img-src data: blob:; font-src 'none'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
SITE_HEADERS = ['Referrer-Policy: no-referrer', 'X-Content-Type-Options: nosniff',
                'Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()', 'Cache-Control: no-cache']
# the 404 page is served under the same policy as the app, so its style is a <style> element whose hash _headers lists
NOT_FOUND_CSS = ('body{margin:0;padding:40px 24px;background:#0e1318;color:#e8ecef;font-family:system-ui,sans-serif}'
                 'h1{font-size:20px;font-weight:600}a{color:#e8ecef}')
NOT_FOUND = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<title>Not found · OceanLens 3D</title>\n'
             '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
             f'<style>{NOT_FOUND_CSS}</style>\n</head>\n<body>\n'
             '<h1>Page not found</h1>\n'
             '<p><a href="/">Open OceanLens 3D</a></p>\n</body>\n</html>\n')

CSS_LINK = re.compile(r'<link rel="stylesheet" href="(css/[\w.-]+\.css)">\n')
JSON_TAG = re.compile(r'<script type="application/json" id="([\w-]+)" src="([\w./-]+\.json)"></script>')
SCRIPT_TAG = re.compile(r'<script src="([\w./-]+\.js)"></script>')
ASSET_URL = re.compile(r"url\('\.\./assets/([\w.-]+)'\)")
MIME = {'.png': 'image/png', '.svg': 'image/svg+xml', '.jpg': 'image/jpeg', '.webp': 'image/webp'}


class BuildError(Exception):
    """The sources cannot be built: a missing or unlisted file, a bad reference, invalid JSON, over budget."""


def read_text(path):
    # line endings are normalised the way the HTML parser does, so script hashes match what it sees
    with open(path, encoding='utf-8', newline='') as f:
        return f.read().replace('\r\n', '\n')


def hash_of(text):
    """A CSP source for an inline element whose text is `text`."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii') + "'"


def rel(path):
    """A path relative to the project root with forward slashes, for messages."""
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, '/')
    except ValueError:              # Windows: --out or --site on another drive has no relative path
        return path.replace(os.sep, '/')


def src_path(ref):
    """A path written in src/index.html (relative to src/) as an absolute path; it must exist."""
    path = os.path.normpath(os.path.join(SRC, ref))
    if not os.path.isfile(path):
        raise BuildError(f'src/index.html refers to {ref}, which does not exist')
    return path


def inline_assets(css, css_file):
    """Replace url('../assets/...') in a stylesheet with data: URIs."""
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
        text = read_text(path)
        if re.search(r'</style|<!--', text, re.I):
            raise BuildError(f'{rel(path)} contains "</style" or "<!--", which would end or confuse the inlined <style>')
        css.append(inline_assets(text, path))
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
        if re.search(r'<!--|<script', code, re.I):
            # "<!--" then "<script" puts the HTML parser in a state where "</script>" no longer ends the element
            raise BuildError(f'{rel(path)} contains "<!--" or "<script", which can swallow the rest of the page')
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
            if not u.startswith(('#', 'data:'))]
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
    if re.search(r'<[a-z][^>]*?\sstyle\s*=', tags, re.I):
        raise BuildError('style="" markup in src/index.html: the policy hashes the <style> element only; use a class')
    hashes = []
    for m in re.finditer(r'<!--.*?-->|<script\b([^>]*)>(.*?)</script>', page, re.S | re.I):
        if m.group(0).startswith('<!--'):
            continue            # a comment can mention <script> without opening one
        attrs, body = m.group(1), m.group(2)
        if 'src=' in attrs or ('type=' in attrs and 'javascript' not in attrs and 'module' not in attrs):
            continue
        hashes.append(hash_of(body))
    # the one inlined <style> is hashed too, so the policy needs no 'unsafe-inline': nothing writes style="" markup (the
    # build refuses it above and the scripts only set element.style, which a policy does not restrict)
    styles = [hash_of(m.group(1)) for m in re.finditer(r'<!--.*?-->|<style\b[^>]*>(.*?)</style>', page, re.S | re.I)
              if not m.group(0).startswith('<!--')]
    csp = CSP.format(scripts=' '.join(hashes), styles=' '.join(styles) or "'none'")
    marker = f'<meta name="version" content="{version}">\n'
    page = page.replace(marker, marker + f'<meta http-equiv="Content-Security-Policy" content="{csp}">\n', 1)

    data = page.encode('utf-8')
    if len(data) > BUDGET:
        raise BuildError(f'{len(data):,} bytes is over the {BUDGET:,} byte budget')
    return data, sizes, version, csp


def write(data, out):
    """Write bytes through a temporary file, so a failed build never leaves half a page."""
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
    """build.py --check: build in memory and compare with the shipped root file; 1 when stale or broken."""
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
    """Build to `out` and print the size of each part."""
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
    csp = csp.replace('style-src ', f'style-src {hash_of(NOT_FOUND_CSS)} ', 1)
    headers = ['/*', f"  Content-Security-Policy: {csp}; frame-ancestors 'none'"] + ['  ' + h for h in SITE_HEADERS]
    write(('\n'.join(headers) + '\n').encode('utf-8'), os.path.join(folder, '_headers'))
    print(f'built site {rel(folder)} {version}: index.html, 404.html, robots.txt, _headers')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=DEFAULT_OUT, help='output file (default: dist/OceanLens3D.html)')
    ap.add_argument('--check', action='store_true', help='check the sources and the shipped file; write nothing')
    ap.add_argument('--site', metavar='DIR', help='write a static site: index.html, 404.html, robots.txt, _headers')
    args = ap.parse_args()
    if args.check:
        return check()
    try:
        if args.site:
            build_site(os.path.abspath(args.site))
        else:
            build_once(os.path.abspath(args.out))
    except BuildError as e:
        print('error: ' + str(e))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
