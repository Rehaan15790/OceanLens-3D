# Third-party notices

OceanLens 3D ships as one HTML file. That file contains the third-party software and data listed here, and each
keeps its own licence or terms of use. The project's own code has no licence yet (see README.md).

## Software

### three.js r128

Inlined in the built file from `vendor/three.r128.min.js`, unmodified.

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

The real-data snapshot embedded in the built file is derived from the datasets below. None of them needs an
account. Each asks to be acknowledged or cited when used.

<!-- science: replace each "to confirm" line with the final acknowledgement text and citation (PLAN 3.1). -->

### HYCOM + NCODA Global 1/12° Analysis (GOFS 3.1, GLBy0.08/expt_93.0)

Temperature, salinity and currents for eight analyses, 4–25 May 2020, averaged into 0.5° cells.
- Acknowledgement: to confirm (HYCOM Consortium wording from hycom.org).
- References: to confirm (GOFS 3.1 and NCODA).

### Argo float data (Argo GDAC, accessed through the Ifremer ERDDAP server)

Temperature and salinity profiles, May 2020, delayed-mode or adjusted values where available.
- Acknowledgement: "These data were collected and made freely available by the International Argo Program and
  the national programs that contribute to it (https://argo.ucsd.edu, https://www.ocean-ops.org). The Argo
  Program is part of the Global Ocean Observing System." (to confirm)
- Citation: Argo (2000). Argo float data and metadata from Global Data Assembly Centre (Argo GDAC). SEANOE.
  https://doi.org/10.17882/42182, accessed on the date recorded in the data provenance. (to confirm)

### ETOPO1 Global Relief Model (NOAA NCEI, through the NOAA ERDDAP server)

Coastline and sea floor, sampled every 0.1°. A US government work in the public domain.
- Citation: Amante, C. and B. W. Eakins (2009). ETOPO1 1 Arc-Minute Global Relief Model: Procedures, Data
  Sources and Analysis. NOAA Technical Memorandum NESDIS NGDC-24. https://doi.org/10.7289/V5C8276M (to confirm)

### IBTrACS v04r01, North Indian basin (NOAA NCEI)

Cyclone Amphan's six-hourly positions and winds, from RSMC New Delhi (IMD) and JTWC. A US government work in the
public domain; the originating agencies should be credited.
- Citations: Knapp, K. R., M. C. Kruk, D. H. Levinson, H. J. Diamond and C. J. Neumann (2010). The International
  Best Track Archive for Climate Stewardship (IBTrACS). Bulletin of the American Meteorological Society 91,
  363–376. https://doi.org/10.1175/2009BAMS2755.1 and the v04 dataset, https://doi.org/10.25921/82ty-9e16
  (to confirm)
