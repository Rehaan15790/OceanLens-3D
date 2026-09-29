# Deploying OceanLens 3D

One build produces two things:

1. **The single file**, `OceanLens3D.html`. It is for USB drives, email and the hackathon submission, and it must
   keep opening by double-click over `file://` with no network.
2. **A static site**: the same file served as `index.html`, with security headers.

The repository is private and has no remote. Everything below assumes a **private** GitHub repository that the owner
may connect later. Connecting any host, or creating any account, is the owner's decision.

## 1. The single file (release)

```
python tools/check.py                       # everything green
python build.py --out OceanLens3D.html      # rebuild the shipped file
python -c "import hashlib; print(hashlib.sha256(open('OceanLens3D.html','rb').read()).hexdigest())"
```

1. Bump `VERSION`.
2. Record the version and SHA-256 in the release notes.
3. Attach the file to a GitHub release. On a private repository, releases are private too.

Anyone receiving the file can check it against the published hash.

## 2. The static site

The site folder is `dist/site/`:

| File | Purpose |
|---|---|
| `index.html` | The built single file, unchanged |
| `404.html` | A plain page with a link back to `/` |
| `robots.txt` | `User-agent: *` / `Disallow:` (or `Disallow: /` while the site is not meant to be public) |
| `_headers` | Security and caching headers (read by Cloudflare Pages and Netlify) |

The favicon is inlined in `index.html` as an SVG data URI, so the single file stays single.

`_headers` (the build fills in the CSP hashes, because they change with every build):

```
/*
  Content-Security-Policy: default-src 'none'; script-src 'sha256-…' 'sha256-…'; style-src 'unsafe-inline'; img-src data: blob:; font-src 'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Cache-Control: no-cache
```

- The same CSP, minus `frame-ancestors`, is also in a `<meta>` tag inside the file, so it applies over `file://`
  and on hosts that cannot set headers.
- `no-cache` makes browsers revalidate the one HTML file (by ETag) on every visit, so a new release is picked up
  at once.

### Primary route: Cloudflare Pages or Netlify (free plan, private repository)

Both deploy from a private GitHub repository on their free plans. Both read `_headers`, serve brotli and gzip,
and give HTTPS.

- **Cloudflare Pages.** Create a project and connect the private repository. Use the build command
  `python build.py --site dist/site` and the output directory `dist/site`. Alternatively, skip the Git connection
  and upload `dist/site/` by drag-and-drop ("Direct Upload").
- **Netlify.** Add a new site from the private repository. Use the build command `python build.py --site dist/site`
  and the publish directory `dist/site`. The alternative is to drag-and-drop `dist/site/` onto the dashboard.

Deploys through the host's own Git integration cost no GitHub Actions minutes.

### Paid-plan option: GitHub Pages

GitHub Pages can publish from a private repository only on a paid plan (Pro, Team or Enterprise). It cannot set
custom headers, so the `<meta>` CSP is the only policy, without `frame-ancestors`. It caches for 10 minutes and serves
gzip. Publish `dist/site/` either from a branch, or with an Actions workflow; the workflow uses metered minutes.

## 3. Continuous integration

`.github/workflows/ci.yml` runs `python tools/check.py --quick` on pull requests and on pushes to `main`. It is
written but not pushed. It is kept lean because Actions minutes and artifact storage are metered on private
repositories:

- one `ubuntu-latest` job (Linux minutes cost half of Windows minutes), with a 15-minute timeout;
- documentation-only changes are skipped (`paths-ignore`);
- a newer push cancels the run in progress (`concurrency`);
- test output and the built file are kept for 7 days;
- the full suite runs only on demand (`workflow_dispatch` with `full: true`).

## 4. What never goes into the repository

- The raw pipeline cache (`data-pipeline/cache/`, tens of MB). It is gitignored. Its SHA-256 manifest can be
  committed; the archive itself stays with the owner.
- Personal absolute paths, CA bundles and certificates, tokens and keys. Docs use placeholders such as
  `<path-to-raw-cache>` and `<your-ca-bundle.pem>`. `tools/check.py` fails on absolute home-folder paths in
  tracked files.
- A `LICENSE` file, until the owner chooses a licence. `tools/check.py` checks this too.
