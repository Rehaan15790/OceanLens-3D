# Third-party notices

OceanLens 3D ships as one HTML file. That file contains the third-party software and data listed here, and each
keeps its own licence or terms of use. The project's own code is all rights reserved (see README.md, Copyright and licence).

## Software

### three.js r128

Inlined in the built file from `vendor/three.r128.min.js`, unmodified, with this notice as a comment in front of it
(also in `vendor/three.LICENSE`).

```
The MIT License

Copyright © 2010-2021 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

### Fonts

None. The app uses the operating system's fonts.

## Data

The real-data snapshots and extras embedded in the built file are derived from the datasets below. None of them needs an
account. Each asks to be acknowledged or cited when used. The same texts are in the app's About panel and in the
snapshot's `meta.citations`.

### HYCOM + NCODA Global 1/12° Analysis

HYCOM + NCODA Global 1/12° Analysis (GOFS 3.1, GLBy0.08/expt_93.0), provided by the HYCOM consortium and the U.S.
Naval Research Laboratory (hycom.org). Chassignet et al. (2007), J. Mar. Syst. 65, 60–83; Cummings (2005),
Q. J. R. Meteorol. Soc. 131, 3583–3604.

### Copernicus Marine GLORYS12V1 reanalysis

`data/amphan-2020-glorys.json` is derived from it. Generated using E.U. Copernicus Marine Service Information:
Global Ocean Physics Reanalysis (GLOBAL_MULTIYEAR_PHY_001_030, GLORYS12V1, daily means), doi:10.48670/moi-00021.
Lellouche, J.-M. et al. (2021), The Copernicus Global 1/12° Oceanic and Sea Ice GLORYS12 Reanalysis, Front. Earth
Sci. 9:698876, doi:10.3389/feart.2021.698876. Used under the Copernicus Marine Service commitments and licence
(https://marine.copernicus.eu/user-corner/service-commitments-and-licence), which allows free use and
redistribution with this acknowledgement. The files were read anonymously over HTTPS; no account was used.

### Argo float data

These data were collected and made freely available by the International Argo Program and the national programs
that contribute to it (https://argo.ucsd.edu, https://www.ocean-ops.org). The Argo Program is part of the Global
Ocean Observing System. Argo (2000), Argo float data and metadata from Global Data Assembly Centre (Argo GDAC),
SEANOE, doi:10.17882/42182; accessed through the Ifremer ERDDAP server.

Chlorophyll-a and dissolved oxygen: BGC-Argo synthetic profiles (Argo GDAC, doi:10.17882/42182) via Ifremer ERDDAP
(ArgoFloats-synthetic-BGC). Bittig, H. C. et al. (2019), A BGC-Argo guide, Front. Mar. Sci. 6:502,
doi:10.3389/fmars.2019.00502. The sample file `samples/bgc-argo-2902120-c227.nc` is one such profile.

### ETOPO1 Global Relief Model

Amante, C. and B. W. Eakins (2009), ETOPO1 1 Arc-Minute Global Relief Model: Procedures, Data Sources and Analysis,
NOAA Technical Memorandum NESDIS NGDC-24, doi:10.7289/V5C8276M. (ETOPO1 is superseded by ETOPO 2022.) A US
government work in the public domain.

### IBTrACS

Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond and C. J. Neumann (2010), The International Best Track
Archive for Climate Stewardship (IBTrACS), Bull. Amer. Meteor. Soc. 91, 363–376, doi:10.1175/2009BAMS2755.1;
IBTrACS v04r01, NOAA NCEI, doi:10.25921/82ty-9e16. Positions and winds from RSMC New Delhi (IMD) and the Joint
Typhoon Warning Center. A US government work in the public domain.

### INCOIS IGORA ocean reanalysis (INCOIS LAS)

`data/amphan-2020-igora.json` is derived from IGORA, the INCOIS Global Ocean ReAnalysis (monthly means, 1979–2023),
Indian National Centre for Ocean Information Services (INCOIS), Ministry of Earth Sciences, Government of India,
exported from the INCOIS Live Access Server (las.incois.gov.in) without an account. Check INCOIS's data policy before
redistributing it outside this project.

### Copernicus Marine global analysis and forecast

`data/forecast-latest.json` (written by `data-pipeline/build_forecast.py`; `meta.case.issued` names the run it
holds) is derived from it. Generated using E.U. Copernicus Marine Service Information: Global Ocean Physics
Analysis and Forecast (GLOBAL_ANALYSISFORECAST_PHY_001_024), doi:10.48670/moi-00016. Used under the Copernicus
Marine Service commitments and licence; read anonymously over HTTPS.

### Copernicus Marine OSTIA SST and Copernicus-GlobColour chlorophyll

`data/products-2020-05.json` is derived from them. Generated using E.U. Copernicus Marine Service Information:
Global Ocean OSTIA Sea Surface Temperature and Sea Ice Reprocessed (SST_GLO_SST_L4_REP_OBSERVATIONS_010_011),
doi:10.48670/moi-00168 (Good, S. et al. (2020), Remote Sens. 12, 720); and Global Ocean Colour
(Copernicus-GlobColour), Bio-Geo-Chemical, L4 from Satellite Observations (OCEANCOLOUR_GLO_BGC_L4_MY_009_104),
doi:10.48670/moi-00281, generated by ACRI-ST. Used under the Copernicus Marine Service commitments and licence
(https://marine.copernicus.eu/user-corner/service-commitments-and-licence). The files were read anonymously over
HTTPS; no account was used.

### RAMA moorings (NOAA PMEL GTMBA)

`data/moorings-2020-05.json` is derived from them. RAMA mooring data from the Global Tropical Moored Buoy Array,
provided by the GTMBA Project Office of NOAA/PMEL. McPhaden, M. J. et al. (2009), RAMA: The Research Moored Array for
African-Asian-Australian Monsoon Analysis and Prediction, Bull. Amer. Meteor. Soc. 90, 459–480,
doi:10.1175/2008BAMS2608.1. Read through NOAA CoastWatch's ERDDAP. A US government work; RAMA is supported by NOAA,
MoES/INCOIS and other partners.

### NOAA Global Drifter Program

`data/amphan-2020-extras.json` (drifters) and `data/drift-validation.json` use it. Elipot, S., Sykulski, A., Lumpkin,
R., Centurioni, L. and Pazos, M. (2022), Hourly location, current velocity, and temperature collected from Global
Drifter Program drifters world-wide, NOAA National Centers for Environmental Information, doi:10.25921/x46c-3620.
Served by NOAA AOML ERDDAP (drifter_hourly_qc) under Creative Commons Attribution 4.0.

### NOAA OISST v2.1, from the INCOIS Live Access Server

`data/amphan-2020-extras.json` (sat.sst) holds NOAA OISST v2.1 (AVHRR only) daily SST, provided by NOAA PSL and
extracted as NetCDF from the INCOIS Live Access Server (las.incois.gov.in). Huang, B. et al. (2021), Improvements of
the Daily Optimum Interpolation Sea Surface Temperature (DOISST) Version 2.1, J. Climate 34, 2923–2939,
doi:10.1175/JCLI-D-20-0166.1. A US government work in the public domain.
