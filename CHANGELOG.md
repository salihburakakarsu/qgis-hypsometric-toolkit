# Changelog

## 1.3.0

- Added a **Draw polygon** button next to the boundary layer chooser. It
  creates a temporary polygon layer and hands the map canvas a digitizing tool,
  so an area of interest can be drawn and analyzed without preparing a
  shapefile first. Polygons accumulate in the layer, and the button untoggles
  itself when QGIS switches to another map tool.
- The run cache key now includes a digest of the boundary geometries, so
  redrawing a polygon no longer returns the previous polygon's cached results.

## 1.2.0

- Added a **Clean…** button. It clears the results table, the curves plot and
  the log, and deletes the cached runs stored in the QGIS profile. Deleting
  cached data asks for confirmation first, showing how many runs and how much
  disk space are involved; clearing the view alone does not.
- Cleaning only ever touches the plugin's own cache folder — files written to a
  custom output folder are left alone.

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
