# Sample files for "Add data"

Drop any of these on the 3D view (or use "Add data") in real-data mode. All three fall inside the map
(6–22° N, 61–92° E) and the analysis window (4–25 May 2020).

| File | Real or synthetic | What it is |
|---|---|---|
| `bgc-argo-2902120-c227.nc` | Real | One BGC-Argo profile: float 2902120, cycle 227, 17 May 2020 11:30 UTC, 15.11° N 66.77° E (Arabian Sea). NetCDF-3 table straight from Ifremer ERDDAP, 172 rows: pressure, temperature, salinity, chlorophyll-a and dissolved oxygen, raw and adjusted, with QC flags. |
| `argo-amphan-samples.csv` | Real (processed) | Three Argo profiles taken from this app's snapshot (`data/amphan-2020.json`), so already QC'd and on the app's levels: 2902120 cycle 227 (with chlorophyll and oxygen), 2902230 cycle 307 and 2902280 cycle 71 (Bay of Bengal, 15–17 May). One row per level; units in the headers. |
| `ctd-synthetic-cast.csv` | **Synthetic** | A made-up CTD cast (id `SYN-CTD-01`, type `ctd`) in the Bay of Bengal on 18 May 2020 06:00, 2–498 dbar, built from a smooth formula to show the CTD plugin. It is not a measurement. |

## Where the real data come from

- `bgc-argo-2902120-c227.nc`: Ifremer ERDDAP, dataset `ArgoFloats-synthetic-BGC` (BGC-Argo synthetic profiles from
  the Argo GDAC), downloaded 2026-10-03 with

  ```
  https://erddap.ifremer.fr/erddap/tabledap/ArgoFloats-synthetic-BGC.nc?platform_number,cycle_number,direction,
    time,latitude,longitude,position_qc,pres,pres_qc,pres_adjusted,pres_adjusted_qc,temp,temp_qc,temp_adjusted,
    temp_adjusted_qc,psal,psal_qc,psal_adjusted,psal_adjusted_qc,chla,chla_qc,chla_adjusted,chla_adjusted_qc,
    doxy,doxy_qc,doxy_adjusted,doxy_adjusted_qc&platform_number="2902120"&cycle_number=227&direction="A"
  ```

  (one line, no spaces). The same file is in `data-pipeline/cache/bgc_sample_2902120_227.nc`; its sha256 is in
  `data-pipeline/cache/manifest.json`. On import only adjusted chlorophyll and oxygen with flags 1, 2, 5 or 8 are kept.
- `argo-amphan-samples.csv`: the Argo and BGC-Argo values in the snapshot (see `data-pipeline/README.md`).

Argo data were collected and made freely available by the International Argo Program and the national programs that
contribute to it (https://argo.ucsd.edu, https://www.ocean-ops.org); Argo GDAC, doi:10.17882/42182.
