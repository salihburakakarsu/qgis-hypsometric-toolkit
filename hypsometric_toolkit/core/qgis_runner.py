"""
Wrapper around the QGIS Processing algorithm
"Raster terrain analysis - Hypsometric curves" (qgis:hypsometriccurves),
plus a small file cache so identical runs can reuse the CSVs already produced.
"""

import glob
import hashlib
import json
import os

from qgis.core import (
    QgsApplication,
    QgsProcessingFeatureSourceDefinition,
)

ALG_ID = "qgis:hypsometriccurves"
MANIFEST_NAME = "run_manifest.json"
SUMMARY_NAME = "hypsometric_results.csv"
PLOT_NAME = "hypsometric_curves.png"


def default_cache_root():
    """Managed cache directory inside the active QGIS profile."""
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(), "hypsometric_toolkit", "cache"
    )


def _file_signature(path):
    try:
        st = os.stat(path)
        return f"{path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return f"{path}|unknown"


def compute_params_hash(dem_layer, boundary_layer, selected_only, step,
                        use_percentage):
    """Stable hash of everything that influences the algorithm output."""
    parts = [
        _file_signature(dem_layer.source()),
        _file_signature(boundary_layer.source().split("|")[0]),
        boundary_layer.source(),
        f"count={boundary_layer.featureCount()}",
        f"step={step}",
        f"pct={bool(use_percentage)}",
    ]
    if selected_only:
        fids = sorted(boundary_layer.selectedFeatureIds())
        parts.append("selected=" + ",".join(str(f) for f in fids))
    else:
        parts.append("selected=all")
    digest = hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def build_params(dem_layer, boundary_layer, selected_only, step,
                 use_percentage, output_dir):
    """Parameter dict for qgis:hypsometriccurves."""
    if selected_only:
        boundary = QgsProcessingFeatureSourceDefinition(
            boundary_layer.id(), selectedFeaturesOnly=True
        )
    else:
        boundary = boundary_layer
    return {
        "INPUT_DEM": dem_layer,
        "BOUNDARY_LAYER": boundary,
        "STEP": float(step),
        "USE_PERCENTAGE": bool(use_percentage),
        "OUTPUT_DIRECTORY": str(output_dir),
    }


def run_algorithm(params, context=None, feedback=None):
    """
    Synchronous run (used headless / in tests). The dialog uses
    QgsProcessingAlgRunnerTask instead so the GUI stays responsive.
    """
    from qgis import processing  # requires the Processing plugin to be loaded
    return processing.run(ALG_ID, params, context=context, feedback=feedback)


def get_algorithm():
    """The registered algorithm instance (None if Processing not loaded)."""
    return QgsApplication.processingRegistry().algorithmById(ALG_ID)


def list_output_csvs(output_dir):
    """Histogram CSVs produced by the algorithm, sorted by name."""
    return sorted(glob.glob(os.path.join(str(output_dir), "histogram_*.csv")))


def write_manifest(output_dir, params_hash, extra=None):
    payload = {"algorithm": ALG_ID, "params_hash": params_hash}
    if extra:
        payload.update(extra)
    path = os.path.join(str(output_dir), MANIFEST_NAME)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


def find_cached_run(output_dir, params_hash):
    """
    Return the list of cached CSVs if output_dir holds a completed run with a
    matching params hash, else None.
    """
    manifest_path = os.path.join(str(output_dir), MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path) as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return None
    if manifest.get("params_hash") != params_hash:
        return None
    csvs = list_output_csvs(output_dir)
    return csvs or None
