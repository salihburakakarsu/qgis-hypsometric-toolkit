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
4. Read the HI values and interpretation in the *Results* tab, view the figure
   in the *Curves plot* tab, and use *Open output folder*, *Export summary
   as…*, or *Save plot as…* to get the files.

The *Log* tab carries the algorithm's own messages — check it if a polygon
produced no output (usually it does not intersect the DEM, or it falls entirely
in NODATA).

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

## Requirements

- QGIS ≥ 3.16 with the Processing plugin enabled (it is, by default).
  Tested on QGIS 3.34 LTR and 3.44 on macOS.
- numpy, which ships with QGIS.
- matplotlib is **optional**. When present it renders the plot; when absent the
  plugin falls back to a built-in Qt renderer that draws the same figure. (Some
  QGIS builds, including 3.44 on macOS, do not bundle matplotlib.)

## Development

```
hypsometric_toolkit/       the plugin itself
├── __init__.py            classFactory()
├── metadata.txt           QGIS plugin metadata
├── plugin.py              menu + toolbar entry points
├── dialog.py              the dialog (built in code, no .ui file)
└── core/
    ├── analysis.py        HI math, CSV reading, summary writing (numpy only)
    ├── qgis_runner.py     Processing wrapper + run cache
    ├── drawing.py         scratch polygon layer for the Draw polygon tool
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

On Linux, `python3 tests/test_plugin.py` usually works if `qgis.core` is
importable. Set `QGIS_APP` (macOS bundle path) or `QGIS_PREFIX_PATH` to point
the test at a specific install.

QGIS 3.44 on macOS ships a Python that needs `PYTHONHOME` to start:

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
