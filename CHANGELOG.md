# Changelog

## 1.1.0

- Plot now uses the Strahler convention (relative area a/A on X, relative
  elevation h/H on Y), with the explanatory footer and outside legend, matching
  the standalone `hypsometric_analysis_v2.py` figures.
- Added a pure-Qt plot renderer used automatically when matplotlib is not
  available in the QGIS Python (e.g. QGIS 3.44 on macOS), so the *Curves plot*
  tab always works.
- Feature IDs follow the script convention (`histogram_X` → `Feature_X`).

## 1.0.0

- Initial release: runs `qgis:hypsometriccurves` on a DEM + polygon layer,
  caches the per-feature CSVs, computes hypsometric integrals (curve
  integration and elevation–relief ratio), classifies landform stage, plots the
  curves, and exports a summary CSV.
