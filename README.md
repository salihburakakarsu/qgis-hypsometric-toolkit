# Hypsometric Analysis Toolkit — QGIS Plugin

A QGIS plugin that automates the Processing Toolbox algorithm **Raster Terrain
Analysis → Hypsometric Curves** (`qgis:hypsometriccurves`) and turns its raw
output into finished hypsometric analysis in one click.

Instead of running the algorithm, hunting for the CSV files it wrote, and then
post-processing them in a separate script, the plugin does all of it inside
QGIS: it runs the algorithm on your DEM and polygons, computes the hypsometric
integral for every feature, classifies each landform's erosional stage, plots
the curves, and writes a summary CSV.

## What it does

1. **Runs the algorithm** on a DEM raster + a boundary polygon layer, with the
   algorithm's own options (elevation step, "% of area instead of absolute
   value") and an extra *selected features only* mode.
2. **Collects and caches** the per-feature `histogram_<layer>_<fid>.csv` files
   (columns `Area,Elevation`) in a managed cache folder or any folder you pick.
   Re-running with unchanged inputs reuses the cached CSVs instead of executing
   the algorithm again.
3. **Computes the hypsometric integral (HI)** for each feature, two ways:
   - *HI (curve)* — trapezoidal integration of the hypsometric curve;
   - *HI (formula)* — the elevation–relief ratio,
     `(mean elevation − min) / (max − min)`, area-weighted.
4. **Classifies** each feature's erosional stage:

   | HI | Stage | Characteristics |
   |----|-------|-----------------|
   | > 0.6 | Youthful | Convex profile, steep slopes |
   | 0.35 – 0.6 | Mature | S-shaped profile, balanced erosion |
   | < 0.35 | Old | Concave profile, advanced erosion |

5. **Plots the curves** in the Strahler convention (relative area a/A on X,
   relative elevation h/H on Y) and shows the figure in the dialog.
6. **Exports** a summary CSV, the plot PNG, and leaves the histogram CSVs in
   place so they can be post-processed with the standalone scripts (see
   [Related projects](#related-projects)). The dialog shows the exact command.

### Why the two HI values differ

They are two estimates of the same quantity, which are identical for continuous
topography but diverge on binned data. Each CSV row assigns the area
accumulated within an elevation bin to the bin's *boundary* elevation, which
biases the area-weighted mean elevation upward by roughly half a step — so
*HI (formula)* typically exceeds *HI (curve)* by about `step / (2 × relief)`.
The two converge as you shrink the elevation step relative to the feature's
relief, and a large gap between them means the step is too coarse for that
feature. Report **HI (curve)**: it is the conventional definition and the one
used for the Youthful/Mature/Old classification.

## Install

**Option A — from ZIP**

1. Build the zip: `./build_zip.sh` → `hypsometric_toolkit.zip`
   (or download one from the repository's releases).
2. In QGIS: *Plugins → Manage and Install Plugins… → Install from ZIP*, choose
   the zip, click *Install Plugin*.

**Option B — copy the folder**

Copy `hypsometric_toolkit/` into your QGIS profile plugin directory, then
restart QGIS:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |

Then enable it under *Plugins → Manage and Install Plugins… → Installed →
Hypsometric Analysis Toolkit*.

## Use

1. Load a DEM raster and a polygon layer (watersheds, craters, basins, …) into
   your project — or skip the polygon layer and draw one, see below.
2. Open the plugin: **Raster menu → Hypsometric Analysis Toolkit**, or the
   toolbar button.
3. Pick the DEM and polygon layers, set the **elevation step** (vertical bin
   size, in DEM units), choose where the output goes, and click **Run
   analysis**.
4. Read the HI values and d/D in the *Results* tab (the Youthful/Mature/Old
   interpretation is on each feature's tooltip, and in the summary CSV), view the figure
   in the *Curves plot* tab, and use *Open output folder*, *Export summary
   as…*, or *Save plot as…* to get the files.

The *Log* tab carries the algorithm's own messages — check it if a polygon
produced no output (usually it does not intersect the DEM, or it falls entirely
in NODATA).

### Depth, diameter and d/D

**Also measure crater depth, diameter and d/D**, in the Output section, appends
standard morphometry to the summary CSV next to the HI columns. The hypsometric
integral on its own cannot say whether the curve carries information beyond
ordinary morphometry — this is what makes that comparison possible.

It uses the rim fitted by *Detect rim* when there is one, and otherwise fits one
inside each boundary polygon, so it works with drawn and prepared polygons too.

Several different quantities get loosely called "depth", so each is reported
separately rather than one being chosen:

| Column | Meaning |
|---|---|
| `depth_rim_to_floor_m` | Rim crest to floor — the crater's depth |
| `depth_alt_floor_m` | The same, under the other floor definition |
| `rim_above_surroundings_m` | Rim crest above the surrounding plain |
| `floor_below_surroundings_m` | Floor below the surrounding plain |
| `elevation_range` (HI columns) | None of these: max minus min inside the polygon, set by two single pixels |

The floor definition dominates the uncertainty, so both are reported. The
default is a low percentile over a wide disc, which ignores wall pixels and
barely moves when its radius changes; the alternative is the mean inside 0.4 ×
the rim radius, which swings by hundreds of metres as that fraction changes
because it starts averaging in the wall.

`d_over_D` is depth over diameter, and `pike1977_predicted_depth_m` gives the
Pike (1977) lunar depth-diameter prediction with the branch used — the simple
and complex branches disagree badly across the transition near 15 km.

### Drawing an area of interest

**Draw polygon**, next to the boundary layer chooser, skips preparing a
boundary layer. It creates a temporary polygon layer called `Drawn_boundary`,
selects it as the boundary, and hands the map canvas a digitizing tool: click
vertices on the map, right-click to close the polygon. Draw as many as you
like — each one becomes a feature and gets its own curve — then click **Stop
drawing** and run the analysis. The button untoggles itself if you switch to
another QGIS map tool.

The layer is an in-memory scratch layer, so nothing is written to disk. To keep
it, use *Layer ▸ Make Permanent* or export it as a shapefile / GeoPackage like
any other layer. It is drawn in the project CRS; the algorithm reprojects it to
the DEM's CRS on its own.

### Detecting a crater rim from the DEM

**Detect rim…**, beside *Draw polygon*, fits the boundary from the elevations
instead of your hand. It seeds the search from the centroid of the lowest
pixels, casts rays on each azimuth and picks the rim crest on each, fits a
circle through those picks by least squares, then re-casts from the fitted
centre and repeats. The fitted circles arrive as a `Crater_rim` polygon layer,
selected as the boundary and ready to analyze.

The seed is only a starting guess: for an asymmetric floor the lowest-pixel
centroid is not the geometric centre of the rim, and the refinement is what
makes the diameter trustworthy.

Two modes:

- **Whole DEM** — finds the deepest feature, one crater per raster.
- **Inside the boundary polygons** — one rim per polygon. Use this when a DEM
  holds more than one crater: a whole-raster seed lands *between* two
  comparable depressions, in neither of them. The polygon selects which crater
  to measure by seeding the search inside it; the rays themselves run on the
  whole raster, so the polygon can be drawn tightly around the crater without
  truncating the profiles that find the rim.

When seeding from polygons the DEM is read **only around each polygon**, at a
resolution taken from that polygon's size, and the rays are capped by it too.
Both matter for small craters: a resolution chosen from the raster is far too
coarse for a 1 km crater in a 45 km DTM, and rays allowed to cross the whole
raster make the wall-finding step skip the crater entirely. Untick *Choose
automatically from the polygon size* to set the resolution yourself.

**Downsample** reads the DEM at 1/N resolution, purely for speed — the fit does
not need full resolution. Each ray needs at least 30 samples, though, so too
high a value finds nothing; the default is scaled to the raster (12 for a NAC
DTM of tens of thousands of pixels, 1–3 for a small one), the dialog shows the
grid it produces, and an over-coarse choice says so rather than failing quietly.

**The rim outline** can be a fitted circle, a traced rim, or both:

- **Fitted circle** — the least-squares circle through the picks. One diameter,
  plus a residual saying how circular the rim actually is.
- **Traced rim** — the picks themselves, connected in azimuth order. Craters
  are often not round, and a circle cannot represent an elliptical rim, so this
  keeps the real outline and an area that is not forced to a circle. On a
  synthetic 2:1 elliptical crater the traced outline recovers the 2.00 aspect
  ratio exactly, where the circle is round by construction.
- **Both** — written into one layer, told apart by the `shape` attribute, so
  the two footprints can be compared in a single analysis run.

Azimuths where no rim was found are interpolated from their neighbours so the
traced ring closes; `n_interpolated` says how many. Both outlines share the
same centre and the same rim picks, so a roughly one-pixel outward bias in the
picks affects them equally — the traced outline improves the *shape*, not that
bias.

Two attributes on each fitted circle say whether to trust it:

| Attribute | Meaning |
|---|---|
| `centre_shift_km` | How far refinement moved the centre from the seed. Large means the seed was poor — and that an unrefined measurement would have been wrong. |
| `fit_rms_km` | Circle-fit residual, i.e. how circular the rim actually is. Large means an elliptical or badly detected rim, so a single "diameter" means little. |

`quality` combines them into `ok`, `LOW CONFIDENCE (n/N rays)` or
`NON-CIRCULAR`, using the same thresholds as the standalone scripts (at least
12 rays, rms under 0.08 × radius).

**Clean…** starts over: it clears the results, plot and log, and deletes the
cached runs held in the QGIS profile. It asks for confirmation before deleting
anything, showing how many runs and how much disk space are involved, and never
touches files you wrote to a custom output folder.

### Outputs, per run

```
<output folder>/
├── histogram_<layer>_<fid>.csv   # one per polygon (raw algorithm output)
├── hypsometric_results.csv       # summary: HI values, elevations, interpretation
├── hypsometric_curves.png        # combined curves plot
└── run_manifest.json             # cache key describing the inputs
```

Managed cache runs live under
`<QGIS profile>/hypsometric_toolkit/cache/<params-hash>/`.

### Warnings about the DEM's CRS and the elevation step

Before each run the DEM's CRS is checked and anything suspect is written to the
*Log* tab, with a short note in the status line. Each warning says what it
affects and what it does not, because **HI is a ratio of areas**: a uniform
scale error cancels out and HI survives it untouched, while absolute areas do
not. The summary CSV records this per run in `area_reliability`.

| Checked | Effect |
|---|---|
| Geographic DEM (degrees) | `Area` is in square degrees, not square metres. HI unaffected. |
| Body-radius mismatch with the project CRS | The plugin's numbers are unaffected, since it measures in the DEM's CRS — but on-screen measurements and other tools will be wrong. |
| Equirectangular DEM far from its `lat_ts` | Pixels are not equal-area; the bias is reported rather than silently corrected. |

The project CRS is deliberately *not* treated as a problem on its own: the
algorithm reprojects boundaries into the raster's CRS and takes areas from the
raster's geotransform, so the project CRS does not change anything reported
here. A projected DEM matching its project raises nothing.

Separately, a feature whose **elevation step is too coarse for its relief** is
flagged: the two HI estimates diverge by roughly `step / (2 x relief)` on
binned data, so a large predicted gap means the step should come down for that
feature.

## Requirements

- QGIS ≥ 3.16 with the Processing plugin enabled (it is, by default).
  Tested on QGIS 3.34 LTR, 3.44 and 3.44.14 on macOS.
- numpy, which ships with QGIS.
- matplotlib is **optional**. When present it renders the plot; when absent the
  plugin falls back to a built-in Qt renderer that draws the same figure. (Some
  QGIS builds, such as the qgis.org 3.44 macOS package, do not bundle
  matplotlib, while others, such as the MacPorts build, do.)

## Development

```
hypsometric_toolkit/       the plugin itself
├── __init__.py            classFactory()
├── metadata.txt           QGIS plugin metadata
├── plugin.py              menu + toolbar entry points
├── dialog.py              the dialog (built in code, no .ui file)
├── rim_dialog.py          parameter dialog for rim detection
└── core/
    ├── analysis.py        HI math, CSV reading, summary writing (numpy only)
    ├── qgis_runner.py     Processing wrapper + run cache
    ├── drawing.py         scratch polygon and fitted-rim layers
    ├── rim.py             crater centre refinement + rim circle fitting
    ├── morphometry.py     depth, diameter and d/D
    ├── crs_check.py       CRS and elevation-step sanity checks
    └── plotting.py        matplotlib and Qt plot renderers
build_zip.sh               packages the plugin for "Install from ZIP"
dev/make_test_data.py      generates a synthetic DEM + boundary polygons
tests/test_plugin.py       headless end-to-end test against a real QGIS
sample_data/               one example algorithm output CSV
```

`core/analysis.py` deliberately avoids pandas, since QGIS does not bundle it on
every platform; it uses only the `csv` module and numpy.

### Running the tests

The test suite drives a real QGIS, so run it with the QGIS-bundled Python
rather than a system or conda Python:

```bash
/Applications/QGIS-LTR.app/Contents/MacOS/bin/python3 tests/test_plugin.py
```

For a MacPorts build, point `QGIS_APP` at the bundle and use the MacPorts
interpreter:

```bash
QGIS_APP=/Applications/MacPorts/QGIS3.app /opt/local/bin/python3.14 tests/test_plugin.py
```

On Linux, `python3 tests/test_plugin.py` usually works if `qgis.core` is
importable. Set `QGIS_APP` (macOS bundle path) or `QGIS_PREFIX_PATH` to point
the test at a specific install.

The qgis.org 3.44 macOS package ships a Python that needs `PYTHONHOME` to
start:

```bash
PYTHONHOME=/Applications/QGIS.app/Contents/Frameworks \
  /Applications/QGIS.app/Contents/MacOS/python3.12 tests/test_plugin.py
```

The test generates a synthetic DEM containing two hills and a crater, runs the
real Processing algorithm on it, and checks the resulting HI values, the cache
hit/miss logic, the summary CSV, and both plot renderers.

## Related projects

The standalone command-line scripts this plugin grew out of, for processing
hypsometric CSVs outside QGIS:

- [hypsometric-analysis](https://github.com/salihburakakarsu/hypsometric-analysis)
- [hypsometric-analysis-toolkit](https://github.com/salihburakakarsu/hypsometric-analysis-toolkit)

The plugin's HI math matches those scripts, and its CSV outputs can be fed
straight into them.

## License

MIT — see [LICENSE](LICENSE).
