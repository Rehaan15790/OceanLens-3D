# Deploying OceanLens 3D

One build produces two things, and a small server adds a third:

1. **The single file**, `OceanLens3D.html`. It is for USB drives, email and the hackathon submission, and it must
   keep opening by double-click over `file://` with no network.
2. **A static site**: the same file served as `index.html`, with security headers.
3. **The data server** (`server/serve.py`): the same file plus a REST API, OGC WMS 1.3.0, WCS 2.0.1 and OPeNDAP DAP2, for an
   institution's own machines (section 4).

The repository is **public** on GitHub so the code can be read and reviewed, but the code is all rights reserved
(README.md, Copyright and licence). Connecting any host, or creating any account, is the owner's decision.

## 1. The single file (release)

```
python tools/check.py                       # passes except the perf load budget (docs/TESTING.md)
python build.py --out OceanLens3D.html      # rebuild the shipped file
python -c "import hashlib; print(hashlib.sha256(open('OceanLens3D.html','rb').read()).hexdigest())"
```

1. Bump `VERSION`.
2. Record the version and SHA-256 in the release notes.
3. Attach the file to a GitHub release. On a public repository, releases are public: anyone can download the file.

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
  Content-Security-Policy: default-src 'none'; script-src 'sha256-…' 'sha256-…' …; style-src 'sha256-…'; img-src data: blob:; font-src 'none'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
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

### Primary route: Cloudflare Pages or Netlify (free plan)

Both deploy from a GitHub repository on their free plans. Both read `_headers`, serve brotli and gzip,
and give HTTPS.

- **Cloudflare Pages.** Create a project and connect the repository. Use the build command
  `python build.py --site dist/site` and the output directory `dist/site`. Alternatively, skip the Git connection
  and upload `dist/site/` by drag-and-drop ("Direct Upload").
- **Netlify.** Add a new site from the repository. Use the build command `python build.py --site dist/site`
  and the publish directory `dist/site`. The alternative is to drag-and-drop `dist/site/` onto the dashboard.

Deploys through the host's own Git integration cost no GitHub Actions minutes.

### Option: GitHub Pages

GitHub Pages publishes from a public repository on the free plan (from a private one only on Pro, Team or
Enterprise). It cannot set custom headers, so the `<meta>` CSP is the only policy, without `frame-ancestors`. It
caches for 10 minutes and serves gzip. Publish `dist/site/` either from a branch, or with an Actions workflow.

A public site lets anyone use the app. While the code is all rights reserved, decide that before publishing one.

## 3. Continuous integration

`.github/workflows/ci.yml` runs `python tools/check.py --quick` (unit, server and smoke suites) on GitHub Actions,
on pull requests and on pushes to `main`. It installs numpy, and `tools/cdp.py` finds the runner's Google Chrome.
Actions minutes are free on public repositories, but it is kept lean for fast feedback and in case the repository
is made private again, where minutes and artifact storage are metered:

- one `ubuntu-latest` job (Linux minutes cost half of Windows minutes), with a 15-minute timeout;
- documentation-only changes are skipped (`paths-ignore`);
- a newer push cancels the run in progress (`concurrency`);
- the test output (when a run fails) or the built file (when it passes) is kept for 7 days;
- the full suite runs only on demand (`workflow_dispatch` with `full: true`).

## 4. The data server on institutional infrastructure (e.g. INCOIS)

`server/serve.py` uses only the Python standard library and numpy. It needs no database, no container and no
internet access. It serves the single file at `/`, the API at `/api/v1`, WMS at `/wms`, WCS at `/wcs` and OPeNDAP
at `/opendap` (`docs/API.md`). The page detects that it is served over HTTP and lists the server's datasets.
Picking one loads its observed profiles or, for another snapshot, reloads the page with that snapshot embedded. A
model file given with `--netcdf` that covers the map and has a time within 12 h of each of the page's analyses also
joins the Model switch: the server puts it on the page's grid and the page streams it when it is chosen
(`/api/v1/datasets/<id>/model`). Other files are served through WMS, WCS, OPeNDAP and REST only. Opened from a file,
the page behaves exactly as before.

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

**Today's forecast, every day.** `data-pipeline/build_forecast.py --fetch` writes the latest Copernicus Marine
forecast to `data/forecast-latest.json` (about 1 GB of byte ranges a day, kept in the cache; prune old runs). Run it
from a timer, and start the server with `--reload 600` so it serves each new run within 10 minutes, without a
restart:

```
# /etc/systemd/system/oceanlens-forecast.service
[Unit]
Description=OceanLens 3D: the latest Copernicus Marine forecast
[Service]
Type=oneshot
User=oceanlens
WorkingDirectory=/opt/oceanlens
ExecStart=/usr/bin/python3 data-pipeline/build_forecast.py --fetch --cache /var/cache/oceanlens

# /etc/systemd/system/oceanlens-forecast.timer
[Timer]
OnCalendar=*-*-* 04:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
```

Add `--reload 600` to the server's `ExecStart`, and `ReadWritePaths=/opt/oceanlens/data /var/cache/oceanlens` to the
forecast service if the server unit's `ProtectSystem=strict` is copied into it. The run is published by about
02:30 UTC; a failed day keeps the previous forecast.

**Put the institution's web server in front.** The server listens on 127.0.0.1 only. TLS, user accounts and access
logs belong to the reverse proxy. The server can also check API keys and limit each client itself:

```
python -c "import secrets; print(secrets.token_urlsafe(24))" >> /etc/oceanlens/keys.txt    # one key per partner
python server/serve.py --api-keys-file /etc/oceanlens/keys.txt --rate-limit 120 --trust-forwarded \
    --public-url https://host/oceanlens
```

Partners then send `Authorization: Bearer <key>` (or `key=<key>` from WMS and OPeNDAP clients); the page opens as
`https://host/oceanlens/?key=<key>`. A key in a URL ends up in the proxy's access log and the browser's history, so
prefer the header and keep that log private. `--trust-forwarded` is only for a server behind the proxy: it
rate-limits by the last address in `X-Forwarded-For`, so the proxy must set that header to the client's address
(without it every user shares one limit). With nginx:

```
location /oceanlens/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    add_header Strict-Transport-Security "max-age=31536000" always;
}
```

Notes:

- `--public-url` must match the address users see, so WMS/WCS capabilities documents point at the proxy rather than
  at 127.0.0.1.
- `--cors` is off by default. Turn it on only for the origins that embed the API (e.g. an existing INCOIS portal
  calling WMS from its own pages).
- Model data: CF NetCDF-3 or NetCDF-4 files given with `--netcdf`. The server takes every numeric variable on 1-D
  latitude and longitude axes (depth and time axes are optional), so a file with a single variable works. It reads
  each file whole, so give it a regional subset, for example a `copernicusmarine subset` download as it comes (that
  toolbox needs a free account). For Copernicus Marine's global native files, use the pipeline's
  `--model glorys` route instead, which reads only the parts over the map (`data-pipeline/README.md`). NetCDF-4
  compressed with szip or zstd is refused. INCOIS LAS NetCDF exports read as they come; `build_las.py` fetches IGORA from
  the LAS for the app itself.
- `data-pipeline/build_snapshot.py --model-netcdf FILE` (NetCDF-3 or NetCDF-4) makes an app snapshot for `data/`, but it needs more: one
  file holding temperature, salinity and both current components (u and v) on 1-D latitude, longitude and depth
  axes, covering the map down to 1000 m. It uses the first 16 time steps. Files with one variable each must be
  merged into one file first.
- `--engine xarray` reads the `--netcdf` files through xarray (NetCDF-3 via scipy, NetCDF-4 via netCDF4 or h5netcdf)
  instead of the project's own readers, for sites that standardise on xarray; the result is the same.
- Health check: `GET /healthz` returns `{"status": "ok", ...}`.
- The server reads its datasets at start-up. With `--reload SECONDS` it also loads snapshot files that change or
  appear (a daily forecast); restart it after changing `--netcdf` files.
- It refuses requests above 2048 × 2048 pixels (WMS) or 5 million cells (WCS). It serves only GET, HEAD and OPTIONS,
  and only fixed routes, so it never serves files from disk by path.
- The single file keeps working with no server at all. The server adds data access; the page does not depend on it.

## 5. What never goes into the repository

- The raw pipeline cache (`data-pipeline/cache/`, where a rebuild looks by default; tens of MB). It is gitignored. Its SHA-256 manifest can be
  committed; the archive itself stays with the owner.
- Personal absolute paths, CA bundles and certificates, tokens and keys. Docs use placeholders such as
  `<path-to-raw-cache>` and `<your-ca-bundle.pem>`. `tools/check.py` fails on absolute home-folder paths in
  tracked files.
- A `LICENSE` file: the code is all rights reserved (README.md). `tools/check.py` checks this too.
