#!/usr/bin/env python3
"""
Headless end-to-end test for the Hypsometric Analysis Toolkit plugin.

It drives a real QGIS: it generates a synthetic DEM with two hills and a
crater, runs the Processing algorithm qgis:hypsometriccurves on it, and checks
the analysis, the run cache, the summary CSV, both plot renderers, and that the
dialog can be built.

Run it with the Python bundled with QGIS, for example:

    /Applications/QGIS-LTR.app/Contents/MacOS/bin/python3 tests/test_plugin.py

Set QGIS_APP (path to a QGIS .app bundle) or QGIS_PREFIX_PATH to point the test
at a particular installation. Outputs are written to tests/output/.
"""

import glob
import os
import shutil
import sys

# Qt must run without a display; this has to be set before QgsApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "tests", "output")

STEP = 5.0
# HI values the synthetic DEM produces, in output-file order (feature 1, 2, 3):
# the steep hill and the broad hill are mostly low ground inside their squares,
# while the crater square is a high plateau with a small deep bowl.
EXPECTED_HI = [0.181, 0.311, 0.671]
# Known values for sample_data/sample_histogram.csv, cross-checked against the
# standalone hypsometric_analysis_v2.py script.
SAMPLE_HI_CURVE = 0.278477
SAMPLE_HI_FORMULA = 0.289956


class Checker:
    """Minimal test harness: prints one line per check, exits non-zero on any failure."""

    def __init__(self):
        self.passed = 0
        self.failed = 0

    def ok(self, label, condition, detail=""):
        if condition:
            self.passed += 1
            print(f"  ok    {label}")
        else:
            self.failed += 1
            suffix = f"  <- {detail}" if detail else ""
            print(f"  FAIL  {label}{suffix}")

    def section(self, title):
        print(f"\n{title}")

    def finish(self):
        total = self.passed + self.failed
        print(f"\n{self.passed}/{total} checks passed")
        if self.failed:
            print("TESTS FAILED")
            return 1
        print("TESTS PASSED")
        return 0


def approx(value, expected, tol=0.01):
    return value is not None and abs(value - expected) <= tol


def _bundle_from_executable():
    """The QGIS .app bundle owning the running interpreter, if any (macOS)."""
    exe = os.path.realpath(sys.executable)
    idx = exe.find(".app/")
    return exe[: idx + len(".app")] if idx != -1 else None


def bootstrap_qgis():
    """
    Make qgis.* and processing importable and return the QGIS prefix path.

    Works when run with the QGIS-bundled Python (the usual case) and when
    qgis.core is already on the path (typical Linux packages).
    """
    bundle = (os.environ.get("QGIS_APP")
              or _bundle_from_executable()
              or (sorted(glob.glob("/Applications/QGIS*.app")) or [None])[0])

    prefix = os.environ.get("QGIS_PREFIX_PATH")

    if bundle and os.path.isdir(bundle):
        resources = os.path.join(bundle, "Contents", "Resources")
        # QGIS 3.34 and earlier keep python/proj/gdal directly under Resources;
        # 3.44 moves them under Resources/qgis. Try both.
        roots = [resources, os.path.join(resources, "qgis")]
        prefix = prefix or os.path.join(bundle, "Contents", "MacOS")
    else:
        roots = ["/usr/share/qgis"]
        prefix = prefix or "/usr"

    for root in roots:
        for sub in ("python", os.path.join("python", "plugins")):
            path = os.path.join(root, sub)
            if os.path.isdir(path) and path not in sys.path:
                sys.path.insert(0, path)
        # keeps PROJ/GDAL from failing to find their databases
        for var, sub in (("PROJ_LIB", "proj"), ("GDAL_DATA", "gdal")):
            path = os.path.join(root, sub)
            if os.path.isdir(path):
                os.environ.setdefault(var, path)

    try:
        import qgis.core  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Could not import qgis.core. Run this test with the Python bundled "
            "with QGIS, or set QGIS_APP / QGIS_PREFIX_PATH.\n"
            f"  interpreter: {sys.executable}\n  error: {exc}"
        )
    return prefix


def main():
    prefix = bootstrap_qgis()

    from qgis.core import QgsApplication, QgsRasterLayer, QgsVectorLayer

    QgsApplication.setPrefixPath(prefix, True)
    app = QgsApplication([], True)  # GUI enabled, offscreen: the dialog needs it
    app.initQgis()

    from processing.core.Processing import Processing
    Processing.initialize()

    sys.path.insert(0, REPO_ROOT)
    sys.path.insert(0, os.path.join(REPO_ROOT, "dev"))
    from hypsometric_toolkit.core import analysis, plotting, qgis_runner
    import make_test_data

    check = Checker()
    print(f"QGIS prefix: {prefix}")
    print(f"Python:      {sys.executable}")
    print(f"matplotlib:  {'yes' if plotting.HAVE_MATPLOTLIB else 'no (Qt renderer)'}")

    if os.path.isdir(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR)

    # ---------------------------------------------------------------- analysis
    check.section("Analysis of the bundled sample CSV")
    sample = os.path.join(REPO_ROOT, "sample_data", "sample_histogram.csv")
    res = analysis.analyze_csv(sample)
    check.ok("HI (curve) matches the reference value",
             approx(res["hypsometric_integral_curve"], SAMPLE_HI_CURVE, 1e-5),
             f"got {res['hypsometric_integral_curve']}")
    check.ok("HI (formula) matches the reference value",
             approx(res["hypsometric_integral_formula"], SAMPLE_HI_FORMULA, 1e-5),
             f"got {res['hypsometric_integral_formula']}")
    check.ok("interpretation is 'Old'",
             res["interpretation"].startswith("Old"), res["interpretation"])
    check.ok("bad input raises ValueError rather than returning garbage",
             _raises_value_error(analysis.analyze_csv, __file__))

    # ------------------------------------------------------------ test dataset
    check.section("Synthetic test data")
    data_dir = os.path.join(OUTPUT_DIR, "test_data")
    dem_path, gpkg_path = make_test_data.generate(data_dir)
    dem = QgsRasterLayer(dem_path, "test_dem")
    boundary = QgsVectorLayer(f"{gpkg_path}|layername=features", "boundaries", "ogr")
    check.ok("DEM layer is valid", dem.isValid())
    check.ok("boundary layer is valid", boundary.isValid())
    check.ok("boundary layer has 3 polygons", boundary.featureCount() == 3,
             f"got {boundary.featureCount()}")

    # -------------------------------------------------------- processing + cache
    check.section("Processing run and cache")
    params_hash = qgis_runner.compute_params_hash(dem, boundary, False, STEP, False)
    run_dir = os.path.join(OUTPUT_DIR, "run", params_hash)
    os.makedirs(run_dir, exist_ok=True)

    check.ok("algorithm qgis:hypsometriccurves is registered",
             qgis_runner.get_algorithm() is not None)
    check.ok("no cache hit before the first run",
             qgis_runner.find_cached_run(run_dir, params_hash) is None)

    params = qgis_runner.build_params(dem, boundary, False, STEP, False, run_dir)
    qgis_runner.run_algorithm(params)
    csvs = qgis_runner.list_output_csvs(run_dir)
    check.ok("algorithm wrote one CSV per polygon", len(csvs) == 3,
             f"got {len(csvs)}: {[os.path.basename(c) for c in csvs]}")

    qgis_runner.write_manifest(run_dir, params_hash, extra={"dem": dem.source()})
    cached = qgis_runner.find_cached_run(run_dir, params_hash)
    check.ok("cache hit once the manifest is written",
             bool(cached) and len(cached) == 3)
    check.ok("cache misses when the parameter hash differs",
             qgis_runner.find_cached_run(run_dir, "0000000000000000") is None)
    check.ok("a percentage run hashes differently from an absolute-area run",
             qgis_runner.compute_params_hash(dem, boundary, False, STEP, True)
             != params_hash)

    # ------------------------------------------------------- end-to-end results
    check.section("Hypsometric integrals from the synthetic DEM")
    results, errors = analysis.analyze_files(csvs)
    check.ok("every CSV was analyzed without errors", not errors, str(errors))
    check.ok("three features analyzed", len(results) == 3, f"got {len(results)}")
    for res, expected in zip(results, EXPECTED_HI):
        hi = res["hypsometric_integral_curve"]
        check.ok(f"{res['feature_id']}: HI ≈ {expected}", approx(hi, expected),
                 f"got {hi:.3f}" if hi is not None else "got None")
    check.ok("feature ids use the Feature_ prefix",
             all(r["feature_id"].startswith("Feature_") for r in results),
             str([r["feature_id"] for r in results]))
    check.ok("the crater square is classified Youthful",
             results[2]["interpretation"].startswith("Youthful"),
             results[2]["interpretation"])

    summary = analysis.write_summary_csv(
        results, os.path.join(run_dir, qgis_runner.SUMMARY_NAME))
    with open(summary) as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    check.ok("summary CSV has a header and one row per feature", len(lines) == 4,
             f"got {len(lines)} lines")
    check.ok("summary CSV header matches the standalone script's columns",
             lines[0] == ",".join(analysis.SUMMARY_FIELDS), lines[0])

    # ------------------------------------------------------------- plotting
    check.section("Plot renderers")
    qt_png = plotting._plot_curves_qt(results, os.path.join(OUTPUT_DIR, "qt_plot.png"))
    check.ok("Qt renderer writes a PNG",
             bool(qt_png) and os.path.getsize(qt_png) > 20000,
             f"{qt_png}: {os.path.getsize(qt_png) if qt_png else 0} bytes")
    if plotting.HAVE_MATPLOTLIB:
        mpl_png = plotting._plot_curves_mpl(
            results, os.path.join(OUTPUT_DIR, "mpl_plot.png"))
        check.ok("matplotlib renderer writes a PNG",
                 bool(mpl_png) and os.path.getsize(mpl_png) > 20000)
    auto_png = plotting.plot_curves(
        results, os.path.join(run_dir, qgis_runner.PLOT_NAME))
    check.ok("plot_curves() picks a renderer automatically",
             bool(auto_png) and os.path.isfile(auto_png))
    check.ok("nothing plottable returns None",
             plotting.plot_curves([], os.path.join(OUTPUT_DIR, "empty.png")) is None)

    # --------------------------------------------------------- cache cleaning
    check.section("Cache cleaning")
    fake_cache = os.path.join(OUTPUT_DIR, "profile", "hypsometric_toolkit", "cache")
    for run_name in ("aaaa1111", "bbbb2222"):
        run_path = os.path.join(fake_cache, run_name)
        os.makedirs(run_path)
        with open(os.path.join(run_path, "histogram_x_1.csv"), "w") as fh:
            fh.write("Area,Elevation\n1.0,10.0\n")

    check.ok("the managed cache folder is recognised",
             qgis_runner.is_managed_cache(fake_cache))
    check.ok("a custom output folder is not treated as the cache",
             not qgis_runner.is_managed_cache(OUTPUT_DIR))

    runs, size = qgis_runner.cache_summary(fake_cache)
    check.ok("cache_summary counts the cached runs", runs == 2, f"got {runs}")
    check.ok("cache_summary reports a non-zero size", size > 0, f"got {size}")

    # the important one: cleaning must never delete a non-cache folder
    guard_dir = os.path.join(OUTPUT_DIR, "not_a_cache")
    guard_file = os.path.join(guard_dir, "keep_me.txt")
    os.makedirs(guard_dir, exist_ok=True)
    with open(guard_file, "w") as fh:
        fh.write("do not delete")
    removed, _freed = qgis_runner.clear_cache(guard_dir)
    check.ok("clear_cache refuses a folder that is not the managed cache",
             removed == 0 and os.path.isfile(guard_file))

    removed, freed = qgis_runner.clear_cache(fake_cache)
    check.ok("clear_cache removes every cached run", removed == 2, f"got {removed}")
    check.ok("clear_cache reports the space it freed", freed > 0, f"got {freed}")
    check.ok("the cache is empty afterwards",
             qgis_runner.cache_summary(fake_cache) == (0, 0))
    check.ok("the cache folder itself survives", os.path.isdir(fake_cache))
    check.ok("format_size is human readable",
             qgis_runner.format_size(1536) == "1.5 KB",
             qgis_runner.format_size(1536))

    # --------------------------------------------------------------- dialog
    check.section("Dialog")
    from hypsometric_toolkit import classFactory
    from hypsometric_toolkit.dialog import HypsometricDialog

    class FakeIface:
        def mainWindow(self):
            return None

    plugin = classFactory(FakeIface())
    check.ok("classFactory returns the plugin object",
             type(plugin).__name__ == "HypsometricToolkitPlugin")

    dlg = HypsometricDialog(iface=FakeIface())
    check.ok("dialog is constructed", dlg.windowTitle().startswith("Hypsometric"))
    _dem, _boundary, error = dlg._validate()
    check.ok("validation rejects an empty layer selection", error is not None)
    dlg._finish(run_dir, csvs, from_cache=True)
    check.ok("results table is populated", dlg.table.rowCount() == 3,
             f"got {dlg.table.rowCount()} rows")
    check.ok("post-processing command is shown",
             "hypsometric_analysis_v2.py" in dlg.cmd_edit.text())
    check.ok("export buttons are enabled once results exist",
             dlg.export_csv_btn.isEnabled() and dlg.open_folder_btn.isEnabled())

    dlg._reset_view()
    check.ok("cleaning empties the results table", dlg.table.rowCount() == 0,
             f"got {dlg.table.rowCount()} rows")
    check.ok("cleaning disables the export buttons",
             not dlg.export_csv_btn.isEnabled()
             and not dlg.save_plot_btn.isEnabled()
             and not dlg.open_folder_btn.isEnabled())
    check.ok("cleaning clears the post-processing command and the log",
             dlg.cmd_edit.text() == "" and dlg.log_edit.toPlainText() == "")
    check.ok("cleaning forgets the output folder", dlg._output_dir is None)

    status = check.finish()
    print(f"Outputs left in: {OUTPUT_DIR}")

    dlg.close()
    dlg.deleteLater()
    del dlg
    app.exitQgis()

    # QGIS tears down Qt and GDAL state at interpreter exit and can segfault
    # doing so, long after the test itself has finished. _exit skips that and
    # keeps the exit status meaningful for CI.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)


def _raises_value_error(func, *args):
    try:
        func(*args)
    except ValueError:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
