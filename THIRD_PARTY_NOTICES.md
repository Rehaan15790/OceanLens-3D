# Third-party notices

OceanLens 3D ships as one HTML file. That file contains the third-party software and data listed here, and each
keeps its own licence or terms of use. The project's own code has no licence yet (see README.md).

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

The real-data snapshot embedded in the built file is derived from the datasets below. None of them needs an
account. Each asks to be acknowledged or cited when used. The same texts are in the app's About panel and in the
snapshot's `meta.citations`.

### HYCOM + NCODA Global 1/12° Analysis

HYCOM + NCODA Global 1/12° Analysis (GOFS 3.1, GLBy0.08/expt_93.0), provided by the HYCOM consortium and the U.S.
Naval Research Laboratory (hycom.org). Chassignet et al. (2007), J. Mar. Syst. 65, 60–83; Cummings (2005),
Q. J. R. Meteorol. Soc. 131, 3583–3604.

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
