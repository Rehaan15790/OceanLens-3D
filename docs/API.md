# OceanLens 3D server API

`server/serve.py` serves the built app and its data over HTTP: a REST API, OGC WMS 1.3.0 and OGC WCS 2.0.1. It needs
Python 3.12 and numpy, nothing else. NetCDF input and output go through `data-pipeline/ncio.py` (NetCDF classic,
CDF-1/2/5).

```
python build.py --out dist/OceanLens3D.html             # the page served at /
python server/serve.py                                  # http://127.0.0.1:8080/, datasets from data/*.json
python server/serve.py --netcdf model.nc --port 8000    # add a CF NetCDF-3 model file
python server/serve.py --host 0.0.0.0 --cors '*' --public-url https://example.org/oceanlens
```

| Option | Meaning |
|---|---|
| `--host`, `--port` | Interface and port. Default `127.0.0.1:8080` (this machine only). |
| `--app FILE` | The single-file app served at `/`. Default `dist/OceanLens3D.html`, then `OceanLens3D.html`. |
| `--data FILE ...` | Schema-1 snapshot JSON files. Default `data/*.json`. |
| `--netcdf FILE ...` | CF NetCDF-3 model files on a rectilinear latitude/longitude grid, with optional depth and time axes. |
| `--cors ORIGIN` | Allow cross-origin requests from this origin (`'*'` for any). Repeatable. Off by default. |
| `--public-url URL` | The base URL clients see, for capabilities documents behind a reverse proxy. |
| `--quiet` | No request log. |

Examples below use `http://localhost:8080` and the dataset `amphan-2020`.

## Conventions

- **Dataset ids** come from file names (`data/amphan-2020.json` is `amphan-2020`). A **layer** or **coverage** is
  `<dataset>.<variable>`, for example `amphan-2020.temp`, and the same name works in WMS and WCS.
- **Variables**: `temp` (sea_water_temperature, degC), `sal` (sea_water_salinity, 1e-3, i.e. PSU), `u` and `v`
  (eastward/northward_sea_water_velocity, m s-1) and `speed` (derived from u and v). NetCDF files keep their own
  variables; CF standard names map onto these keys where they match (e.g. `sea_water_potential_temperature` is `temp`).
- **Time**: ISO 8601 UTC (`2020-05-16T12:00Z`), or an index into the dataset's times. The nearest time is used if it
  is within 1.5 days (or half the time step, if larger); otherwise the request fails.
- **Depth**: metres, positive down, linear between model levels. In WMS, `ELEVATION` is height (negative below the
  surface); a positive value is read as a depth.
- **No data**: cells without model water are `null` in JSON, transparent in maps, `_FillValue` in NetCDF and empty in
  CSV. The snapshot fills such cells from neighbours for drawing; the API does not report those fills.
- **Errors**: REST answers `{"error": {"code": "...", "message": "..."}}` with a 4xx/5xx status. WMS answers a
  `ServiceExceptionReport`, WCS an `ows:ExceptionReport`.
- Responses over 1.4 kB are gzipped when the client accepts it. Only `GET`, `HEAD` and `OPTIONS` are served.

## REST

| Request | Returns |
|---|---|
| `GET /api/v1/datasets` | `{"datasets": [{id, title, source, default, times, depths, bbox, profiles, variables, links}]}`. `default` marks the dataset embedded in the page. |
| `GET /api/v1/datasets/{id}/meta` | The summary plus the grid (`lat`, `lon`), each variable's data range, and the dataset's sources, citations and provenance. |
| `GET /api/v1/datasets/{id}/snapshot` | The schema-1 snapshot JSON, byte for byte (snapshot datasets only). |
| `GET /api/v1/datasets/{id}/field?var=&time=&depth=` | One horizontal field: `{var, units, time, depth, lat, lon, values[lat][lon]}`, south to north. |
| `...&format=bin` | The same field as little-endian Int16, `value = offset + q * scale`, `-32768` = no data. Headers: `X-OceanLens-Shape` (`nlat,nlon`), `-Offset`, `-Scale`, `-Missing`, `-Time`, `-Depth`. |
| `GET /api/v1/datasets/{id}/column?lat=&lon=&time=` | Every variable at every level at one point (bilinear): `{depth, units, values: {temp: [...], ...}}`. |
| `GET /api/v1/datasets/{id}/profiles?bbox=w,s,e,n&start=&end=&type=` | Observed profiles: `{count, profiles: [{id, platform, type, cycle, lat, lon, time, mode, depth, temp, sal, chl?, oxy?}]}`. The app's import accepts this shape as it is. |
| `GET /api/v1/datasets/{id}/track` | The storm track: `{name, fields, fixes}`. |
| `GET /` and `/?dataset={id}` | The app; with `dataset`, the same page with that dataset's snapshot embedded. |
| `GET /healthz` | `{"status": "ok", "version", "datasets", "netcdf"}` for monitoring. |

```
curl http://localhost:8080/api/v1/datasets
curl "http://localhost:8080/api/v1/datasets/amphan-2020/field?var=temp&time=2020-05-16T12:00Z&depth=50"
curl "http://localhost:8080/api/v1/datasets/amphan-2020/column?lat=15&lon=88&time=4"
curl "http://localhost:8080/api/v1/datasets/amphan-2020/profiles?bbox=80,10,92,22&start=2020-05-15&end=2020-05-21"
```

## OGC WMS 1.3.0 (`/wms`, or `/ows?SERVICE=WMS`)

- **GetCapabilities** lists one layer per dataset variable, with `CRS:84` and `EPSG:4326`, a `time` dimension (every
  analysis time) and an `elevation` dimension (every model level), and one style per app palette.
- **GetMap** draws a PNG (RGBA, zlib) with the app's palettes. Land and cells without data are transparent with
  `TRANSPARENT=TRUE`, else `BGCOLOR` (default white). One layer per request; up to 2048 × 2048 pixels.
  - `STYLES`: a palette name (`temperature`, `salinity`, `speed`, `heat`, `depth`, ...) or empty for the variable's own.
  - `COLORSCALERANGE=min,max` sets the colour range (default: the variable's default range); values outside clamp
    to the end colours. `LOGSCALE=true` places values by log10 (min must be above 0).
  - `EPSG:4326` in WMS 1.3.0 takes `BBOX=minLat,minLon,maxLat,maxLon`; `CRS:84` takes longitude first.
- **GetFeatureInfo** returns the values under pixel `I,J` for each of `QUERY_LAYERS`, as `application/json` or
  `text/plain`.

```
http://localhost:8080/wms?SERVICE=WMS&REQUEST=GetCapabilities
http://localhost:8080/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=amphan-2020.temp&STYLES=&CRS=CRS:84&BBOX=61,6,92,22&WIDTH=620&HEIGHT=320&FORMAT=image/png&TRANSPARENT=TRUE&TIME=2020-05-19T12:00:00Z&ELEVATION=-50&COLORSCALERANGE=24,31
http://localhost:8080/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo&LAYERS=amphan-2020.temp&QUERY_LAYERS=amphan-2020.temp,amphan-2020.sal&STYLES=&CRS=CRS:84&BBOX=61,6,92,22&WIDTH=620&HEIGHT=320&I=540&J=120&INFO_FORMAT=application/json&TIME=2020-05-19T12:00:00Z
```

QGIS: *Layer > Add Layer > Add WMS/WMTS Layer*, new connection with the URL `http://localhost:8080/wms`; the time
dimension shows in the layer's temporal settings.

## OGC WCS 2.0.1 (`/wcs`, or `/ows?SERVICE=WCS`), KVP over GET

- **GetCapabilities** lists one `GridCoverage` per dataset variable with its WGS84 bounding box.
- **DescribeCoverage** gives the envelope (axes `time depth Lat Long`), the grid size and the range type (CF standard
  name as the SWE definition, UCUM unit).
- **GetCoverage** returns the coverage, trimmed with `SUBSET`:
  - `SUBSET=Lat(10,15)`, `SUBSET=Long(80,90)`, `SUBSET=depth(0,200)`, `SUBSET=time("2020-05-16T12:00Z","2020-05-22T12:00Z")`;
    `*` leaves an end open. Axis names are case-insensitive (`lat`, `lon`, `latitude`, `longitude` also work).
  - A single value (`SUBSET=time("2020-05-16T12:00Z")`) takes the nearest grid value and keeps that axis with
    length 1, rather than dropping it.
  - `FORMAT=application/netcdf` (default): CF-1.8 NetCDF-3 with `time` (hours since 1970-01-01), `depth`
    (positive down), `lat`, `lon` and the variable as float32 with `standard_name`, `units` and `_FillValue`.
    `FORMAT=text/csv`: one row per cell, `time,depth_m,lat,lon,<var>_<units>`.
  - Up to 5 million cells per request (1 million for CSV).

```
http://localhost:8080/wcs?SERVICE=WCS&REQUEST=GetCapabilities
http://localhost:8080/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=DescribeCoverage&COVERAGEID=amphan-2020.temp
http://localhost:8080/wcs?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=amphan-2020.temp&SUBSET=Lat(10,20)&SUBSET=Long(80,92)&SUBSET=depth(0,200)&FORMAT=application/netcdf
```

Read the NetCDF with any CF tool (`ncdump -h`, xarray, Panoply), or with `data-pipeline/ncio.py`:
`ncio.read('amphan-2020.temp.nc')`.

## What it does not do

- No OPeNDAP (DAP2/DAP4) endpoint. The data pipeline reads HYCOM over OPeNDAP; the server offers REST, WMS and WCS.
- No WMS `GetLegendGraphic`, no reprojection beyond `CRS:84`/`EPSG:4326`, no WCS `rangeSubset`, scaling or
  POST/SOAP bindings, no WCS-T.
- No authentication, rate limiting or TLS of its own: put it behind the institution's reverse proxy
  (`docs/DEPLOY.md`, section 4).
