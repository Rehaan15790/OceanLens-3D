# Deploying OceanLens 3D

One build produces two things, and a small server adds a third:

1. **The single file**, `OceanLens3D.html`. It is for USB drives, email and the hackathon submission, and it must
   keep opening by double-click over `file://` with no network.
2. **A static site**: the same file served as `index.html`, with security headers.
3. **The data server** (`server/serve.py`): the same file plus a REST API, OGC WMS 1.3.0, WCS 2.0.1 and OPeNDAP DAP2, for an
   institution's own machines (section 4).

The repository is a **private** GitHub repository, and everything below assumes it stays private. Connecting any
host, or creating any account, is the owner's decision.

## 1. The single file (release)

```
python tools/check.py                       # passes except the perf load budget (docs/TESTING.md)
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

The favicon is an inline SVG data URI in `index.html`; the build refuses external URLs, so the single file stays
single.

`_headers` (the build fills in the CSP hashes, because they change with every build):

```
/*
  Content-Security-Policy: default-src 'none'; script-src 'sha256-…' 'sha256-…' …; style-src 'unsafe-inline'; img-src data: blob:; font-src 'none'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Cache-Control: no-cache
```

- The same CSP, minus `frame-ancestors`, is also in a `<meta>` tag inside the file, so it applies over `file://`
  and on hosts that cannot set headers. `script-src` lists one hash per inline script. `connect-src 'self'` lets a
  page served by `server/serve.py` call only its own server.
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

`.github/workflows/ci.yml` runs `python tools/check.py --quick` (unit, server and smoke suites) on GitHub Actions,
on pull requests and on pushes to `main`. It installs numpy, and `tools/cdp.py` finds the runner's Google Chrome.
It is kept lean because Actions minutes and artifact storage are metered on private repositories:

- one `ubuntu-latest` job (Linux minutes cost half of Windows minutes), with a 15-minute timeout;
- documentation-only changes are skipped (`paths-ignore`);
- a newer push cancels the run in progress (`concurrency`);
- the test output (when a run fails) or the built file (when it passes) is kept for 7 days;
- the full suite runs only on demand (`workflow_dispatch` with `full: true`).

## 4. The data server on institutional infrastructure (e.g. INCOIS)

`server/serve.py` uses only the Python standard library and numpy. It needs no database, no container and no
internet access. It serves the single file at `/`, the API at `/api/v1`, WMS at `/wms`, WCS at `/wcs` and OPeNDAP
at `/opendap` (`docs/API.md`). The page detects that it is served over HTTP and lists the server's datasets.
Picking one loads its observed profiles or, for another snapshot, reloads the page with that snapshot embedded. The
3D view draws only the snapshot in the page: a model file given with `--netcdf` is served through WMS, WCS, OPeNDAP
and REST, not drawn in 3D. Opened from a file, the page behaves exactly as before.

```
python build.py --out dist/OceanLens3D.html
python server/serve.py --host 127.0.0.1 --port 8080 --netcdf <path-to-model.nc> --quiet
```

**Run it as a service** (systemd; adjust paths and the user):

```
# /etc/systemd/system/oceanlens.service
[Unit]
Description=OceanLens 3D data server
After=network.target

[Service]
User=oceanlens
WorkingDirectory=/opt/oceanlens
ExecStart=/usr/bin/python3 server/serve.py --host 127.0.0.1 --port 8080 --public-url https://<host>/oceanlens --quiet
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

**Put the institution's web server in front.** The server listens on 127.0.0.1 only. TLS, authentication, access
logs and rate limits belong to the reverse proxy. With nginx:

```
location /oceanlens/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_set_header Host $host;
    add_header Strict-Transport-Security "max-age=31536000" always;
}
```

Notes:

- `--public-url` must match the address users see, so WMS/WCS capabilities documents point at the proxy rather than
  at 127.0.0.1.
- `--cors` is off by default. Turn it on only for the origins that embed the API (e.g. an existing INCOIS portal
  calling WMS from its own pages).
- Model data: CF NetCDF-3 files given with `--netcdf`. The server takes every numeric variable on 1-D latitude and
  longitude axes (depth and time axes are optional), so a file with a single variable works. NetCDF-4 files are
  refused. Copernicus Marine's native files (for example GLOBAL_MULTIYEAR_PHY_001_030) are NetCDF-4/HDF5. Download
  a regional subset with `copernicusmarine subset --netcdf3-compatible` (that toolbox needs a free account), or
  convert a NetCDF-4 subset with `nccopy -k classic`. The raw files can also be downloaded over HTTPS without an
  account, but each holds the whole globe for one day, so it must first be cut to the region with another tool and
  then converted to NetCDF-3. A real INCOIS LAS export has not been tried.
- `data-pipeline/build_snapshot.py --model-netcdf FILE` makes an app snapshot for `data/`, but it needs more: one
  file holding temperature, salinity and both current components (u and v) on 1-D latitude, longitude and depth
  axes, covering the map down to 1000 m. It uses the first 16 time steps. Files with one variable each must be
  merged into one file first.
- Health check: `GET /healthz` returns `{"status": "ok", ...}`.
- The server reads its datasets at start-up. Restart it after changing files.
- It refuses requests above 2048 × 2048 pixels (WMS) or 5 million cells (WCS). It serves only GET, HEAD and OPTIONS,
  and only fixed routes, so it never serves files from disk by path.
- The single file keeps working with no server at all. The server adds data access; the page does not depend on it.

## 5. What never goes into the repository

- The raw pipeline cache (`data-pipeline/cache/`, where a rebuild looks by default; tens of MB). It is gitignored. Its SHA-256 manifest can be
  committed; the archive itself stays with the owner.
- Personal absolute paths, CA bundles and certificates, tokens and keys. Docs use placeholders such as
  `<path-to-raw-cache>` and `<your-ca-bundle.pem>`. `tools/check.py` fails on absolute home-folder paths in
  tracked files.
- A `LICENSE` file, until the owner chooses a licence. `tools/check.py` checks this too.
