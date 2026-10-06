# Sample files for "Add data"

Drop any of these on the 3D view (or use "Add data") in real-data mode. All fall inside the map (6–22° N, 61–92° E).
Four fall inside the analysis window (4–25 May 2020). For those the timeline moves to the nearest analysis after
import. The Argo float from 2026 and the two 2016 gliders fall outside it, so "Show all times" switches on. In both
cases the first new profile opens.

| File | Real or synthetic | What it is |
|---|---|---|
| `bgc-argo-2902120-c227.nc` | Real | One BGC-Argo profile: float 2902120, cycle 227, 17 May 2020 11:30 UTC, 15.11° N 66.77° E (Arabian Sea). NetCDF-3 table straight from Ifremer ERDDAP, 172 rows: pressure, temperature, salinity, chlorophyll-a and dissolved oxygen, raw and adjusted, with QC flags. |
| `argo-amphan-samples.csv` | Real (processed) | Three Argo profiles taken from this app's snapshot (`data/amphan-2020.json`), so already QC'd and on the app's levels: 2902120 cycle 227 (with chlorophyll and oxygen), 2902230 cycle 307 and 2902280 cycle 71 (Bay of Bengal, 15–17 May). One row per level; units in the headers. |
| `argo-5907326_prof.nc` | Real, unmodified | An Argo GDAC multi-profile file as the GDAC serves it: float 5907326 (INCOIS, Argo India), cycles 1–4, 4 Sep – 3 Oct 2026, 19.79–20.48° N 64.96–65.28° E (Arabian Sea), real-time data, 56–104 levels from the surface to 1965–2030 dbar. Its `HISTORY_*` variables use the record dimension `N_HISTORY` with no records, the layout of every `<WMO>_prof.nc`. 4 profiles. |
| `argo-erddap-2020-05-18.csv` | Real, unmodified | ERDDAP's default `.csv`, so a units row sits under the header: three Argo profiles in the Bay of Bengal (2902234 cycle 306, 18 May 04:27 UTC, 13.15° N 82.90° E; 2902285 cycle 327, 18 May 13:57, 15.85° N 82.93° E; 2902236 cycle 213, 19 May 01:07, 17.92° N 86.45° E), pressure, temperature and salinity with their QC flags, 475 rows. 3 profiles. |
| `glider-humpback504-2016-07-05.nc` | Real, thinned by the server | An OceanGliders GDAC ERDDAP table (`.nc`, `row` dimension, `cdm_data_type` Trajectory, no profile id). It holds Seaglider Humpback_504 in the BoBBLE experiment, 5 Jul 2016 04:36–14:31 UTC, from one surfacing to the one two dives later, about 8.10° N 85.59° E, one sample per minute (568 rows), PRES, TEMP and PSAL. The QC columns are all missing in this dataset, and `time` carries `valid_max` 90000. 4 profiles: 2 dives and 2 climbs, from near the surface to about 1000 m. |
| `glider-marlin505-2016-07-06.csv` | Real, thinned by the server | The same OceanGliders table as ERDDAP's default `.csv` (with the units row): Seaglider Marlin_505 in BoBBLE, 6 Jul 2016 00:11–10:46 UTC, from one surfacing to the one three dives later, about 7.89° N 89.01° E, one sample per minute (601 rows), with the empty QC columns. 6 profiles: 3 dives and 3 climbs, from the surface to about 1000 m. |
| `ctd-synthetic-cast.csv` | **Synthetic** | A made-up CTD cast (id `SYN-CTD-01`, type `ctd`) in the Bay of Bengal on 18 May 2020 06:00, 2–498 dbar, built from a smooth formula to show the CTD plugin. It is not a measurement. |

## Where the real data come from

`bgc-argo-2902120-c227.nc` and `argo-erddap-2020-05-18.csv` were downloaded on 2026-10-03 (the CSV again, byte for
byte the same, on 2026-10-06); the others on 2026-10-06. Each query below is one line with no spaces. The ERDDAP
queries are written unencoded: `"`, `>`, `<` and `|` must be percent-encoded when the query is sent.

- `bgc-argo-2902120-c227.nc`: Ifremer ERDDAP, dataset `ArgoFloats-synthetic-BGC` (BGC-Argo synthetic profiles from
  the Argo GDAC):

  ```
  https://erddap.ifremer.fr/erddap/tabledap/ArgoFloats-synthetic-BGC.nc?platform_number,cycle_number,direction,
    time,latitude,longitude,position_qc,pres,pres_qc,pres_adjusted,pres_adjusted_qc,temp,temp_qc,temp_adjusted,
    temp_adjusted_qc,psal,psal_qc,psal_adjusted,psal_adjusted_qc,chla,chla_qc,chla_adjusted,chla_adjusted_qc,
    doxy,doxy_qc,doxy_adjusted,doxy_adjusted_qc&platform_number="2902120"&cycle_number=227&direction="A"
  ```

  The same file is in `data-pipeline/cache/bgc_sample_2902120_227.nc`, and its sha256 is in
  `data-pipeline/cache/manifest.json`. On import, only adjusted chlorophyll and oxygen with flags 1, 2, 5 or 8 are
  kept.
- `argo-amphan-samples.csv`: the Argo and BGC-Argo values in the snapshot (see `data-pipeline/README.md`).
- `argo-5907326_prof.nc`: the Argo GDAC (Ifremer mirror), byte for byte. The GDAC rewrites this file as the float
  reports new cycles, so a fresh download will hold more profiles than this copy.

  ```
  https://data-argo.ifremer.fr/dac/incois/5907326/5907326_prof.nc
  ```

  sha256 `daa94773fdb10aa6813ffb2a6e5c3c22427e85f25b451a8f6a87d0919e3348a6`. The real-time QC flags 5 temperature
  samples 3 or 4 (cycles 1, 2 and 4); the import leaves them out.
- `argo-erddap-2020-05-18.csv`: Ifremer ERDDAP, dataset `ArgoFloats` (core Argo profiles from the Argo GDAC):

  ```
  https://erddap.ifremer.fr/erddap/tabledap/ArgoFloats.csv?platform_number,cycle_number,time,latitude,longitude,
    pres,pres_qc,temp,temp_qc,psal,psal_qc&platform_number=~"(2902234|2902236|2902285)"
    &time>=2020-05-18T00:00:00Z&time<=2020-05-19T12:00:00Z
  ```

  These are real-time values. 2902285 has 3 salinity samples flagged 4 (bad); the import leaves them out.
- `glider-humpback504-2016-07-05.nc` and `glider-marlin505-2016-07-06.csv`: Ifremer ERDDAP, dataset
  `OceanGlidersGDACTrajectories` (the OceanGliders GDAC; EGO processing chain, doi:10.17882/45402). The server
  thins the samples to one per minute with `orderByClosest`. Each time window starts and ends at a surfacing, so
  every dive and climb is whole:

  ```
  https://erddap.ifremer.fr/erddap/tabledap/OceanGlidersGDACTrajectories.nc?platform_deployment,time,latitude,
    longitude,PRES,TEMP,PSAL,PRES_QC,TEMP_QC,PSAL_QC&platform_deployment="Humpback_504"
    &time>=2016-07-05T04:36:00Z&time<2016-07-05T14:31:00Z&orderByClosest("platform_deployment,time,60 seconds")
  https://erddap.ifremer.fr/erddap/tabledap/OceanGlidersGDACTrajectories.csv?platform_deployment,time,latitude,
    longitude,PRES,TEMP,PSAL,PRES_QC,TEMP_QC,PSAL_QC&platform_deployment="Marlin_505"
    &time>=2016-07-06T00:00:00Z&time<2016-07-06T10:46:00Z&orderByClosest("platform_deployment,time,60 seconds")
  ```

  The `.nc` file's global attributes (title, abstract, project UFO) describe the whole aggregated dataset; ERDDAP
  copies them from one of its member files. They do not describe these gliders: `platform_deployment` names the
  glider. The five BoBBLE Seagliders (Bellatrix_368, Denebola_382, Humpback_504, Marlin_505, Melonhead_506; 30 Jun –
  20 Jul 2016, about 8° N, 85–89° E) are the only glider deployments the GDAC holds inside the map.
  - On import, each glider's samples are split into dives and climbs where the pressure turns round, and the samples
    are put in 1 m bins.
  - Temperature and salinity are the gliders' near-real-time values. The dataset gives no QC flags for them.

Argo data were collected and made freely available by the International Argo Program and the national programs that
contribute to it (https://argo.ucsd.edu, https://www.ocean-ops.org); Argo GDAC, doi:10.17882/42182.

The glider data were collected during BoBBLE (Bay of Bengal Boundary Layer Experiment, a joint project of India's
Ministry of Earth Sciences and the UK Natural Environment Research Council; Vinayachandran et al. 2018, Bull. Amer.
Meteor. Soc. 99, 1569–1587, doi:10.1175/BAMS-D-16-0230.1). Its five Seagliders, three from the University of East
Anglia (UEA) glider facility and two from the UK's MARS national facility, were deployed and piloted by UEA and
associated personnel. The delayed-mode data set is Webber, B., Matthews, A., Queste, B., Lee, G., Cobas-Garcia, M.,
Heywood, K. and Vinayachandran, P. N. (2019): Ocean glider data from BoBBLE (Bay of Bengal Boundary Layer Experiment)
in July 2016, British Oceanographic Data Centre, doi:10.5285/996bf53d-5448-297a-e053-6c86abc0b996. The near-real-time
data here are distributed by the OceanGliders GDAC (https://www.oceangliders.org) through Ifremer ERDDAP. The
dataset licence asks users to cite the data and to contact the PI before commercial use.
