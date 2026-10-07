# Changelog

## 1.7.1

- **Fixed: polygon-seeded rim detection gave a badly wrong diameter and depth
  when the polygon was drawn around the crater.** Rays were confined to the
  polygon, so every elevation profile was truncated at its edge — and a polygon
  drawn round a crater ends at the rim, exactly where the crest's outward
  turnover is. Almost every ray reported "no crest within reach", the fit fell
  back to a median of the two that survived, and with fewer than five rim
  points the centre never refined at all, leaving it on the floor centroid.
  On a real 21.96 km crater this returned 14.51 km and a depth 1.3 km too
  shallow.
- The polygon now does only what it was introduced for: it **seeds** the
  search, selecting which crater to measure. The rays run on the whole raster,
  so the rim and its turnover are reachable. Both modes now agree with the
  standalone scripts on that crater to within 0.01 km and 1 m.
- A flagged rim fit (`LOW CONFIDENCE`, `NON-CIRCULAR`) is now called out in the
  status line rather than only in the log, since its diameter and depth should
  not be trusted.

## 1.7.0

- The DEM's CRS is now checked before each run, and anything suspect is
  reported in the Log tab and summarised in the status line:
  - a **geographic DEM** (degrees), whose Area column is in square degrees
    rather than square metres;
  - a **body-radius mismatch** between the DEM and the project, e.g. lunar data
    with the project left on an Earth CRS;
  - an **equirectangular DEM far from its `lat_ts`**, whose pixels are not
    equal-area, with the size of the bias reported rather than silently
    corrected.
- Each warning says what is affected and what is not. HI is a ratio of areas,
  so a uniform scale error cancels and HI survives it; absolute areas do not.
  The summary CSV gains an `area_reliability` column recording this per run.
- Scoped by measurement: the project CRS was verified not to affect anything
  this plugin reports, because the algorithm reprojects boundaries into the
  raster's CRS and takes areas from the raster's geotransform. A projected DEM
  matching its project therefore raises nothing at all.
- The gap between *HI (curve)* and *HI (formula)* is now flagged automatically
  when the elevation step is too coarse for a feature's relief, instead of
  being something to notice in the README.

## 1.6.1

- The Results tab now shows **d/D** beside the two HI columns, so the
  comparison can be read without opening the summary CSV.
- The **Interpretation** column has left the table, which was its widest
  column. It is still written to the summary CSV, and is now on the tooltip of
  each feature's name.

## 1.6.0

- The summary CSV can now carry **crater morphometry beside the hypsometric
  integral**: rim-to-floor depth, diameter and d/D, plus the rim and floor
  elevations, the rim's height above the surrounding plain, the floor's depth
  below it, and the Pike (1977) predicted depth with the branch used. HI alone
  cannot say whether the hypsometric curve adds anything beyond standard
  morphometry; this is the comparison that can.
- Enabled by a checkbox in Output. It uses the rim already fitted by Detect rim
  when there is one, and otherwise fits one inside each boundary polygon, so it
  works for drawn and prepared polygons too.
- Two floor definitions are reported, not one: a low percentile over a wide
  disc (the default, barely sensitive to its radius) and the mean inside
  0.4 x the rim radius, which swings by hundreds of metres as that fraction
  changes because it starts averaging in the wall. Keeping both keeps the
  sensitivity visible.
- The HI columns keep their names, order and position, and stay blank-padded
  rather than absent when morphometry was not measured, so the output remains
  interchangeable with the standalone scripts.

## 1.5.0

- Rim detection can now output a **traced rim** — the detected rim picks
  connected in azimuth order — as well as the fitted circle. Craters are often
  not round, and a circle cannot represent an elliptical rim; the traced
  outline keeps the real shape, so its area is not forced to a circle.
- The outline is chosen in the detection dialog: fitted circle (the previous
  behaviour, still the default), traced rim, or both in one layer distinguished
  by a `shape` attribute.
- Azimuths where no rim was found are interpolated from their neighbours so the
  traced ring still closes; `n_interpolated` records how many, and too few
  successful rays yields no traced outline rather than a guess.
- New attributes on every rim feature: `shape`, `area_km2` and `eq_radius_km`,
  so a traced outline's area can be compared with the circle's directly.

## 1.4.1

- The rim detection **downsample** default now scales to the raster. The
  scripts' value of 12 suits LROC NAC DTMs of tens of thousands of pixels, but
  on a small raster it left a grid too coarse for any ray to reach the 30
  samples one needs, so detection found nothing with no explanation.
- An over-coarse downsample now says so, with the search span in pixels and
  what to change, instead of a generic hint.
- The options dialog shows the grid size and pixel size the chosen downsample
  produces, and warns when it looks too coarse.

## 1.4.0

- Added a **Detect rim…** button that fits a crater rim circle from the DEM, so
  the boundary no longer has to be drawn by hand. It seeds from the floor
  centroid, casts rays on each azimuth to pick the rim crest, fits a circle by
  least squares, and re-casts from the fitted centre a few times.
- Two modes: over the whole DEM (finds the deepest feature) or seeded from
  boundary polygons (one rim per polygon). The polygon mode exists because a
  whole-raster seed lands between two comparable depressions, in neither
  crater.
- The fitted circles land in a `Crater_rim` layer carrying `centre_shift_km`
  and `fit_rms_km` — a large shift means the floor centroid was a poor centre,
  a large rms means the rim is not actually circular — plus radius, diameter,
  ray counts and a confidence flag.
- Algorithm and defaults are ported from the standalone morphometry scripts, so
  a rim fitted here matches one fitted there.

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
