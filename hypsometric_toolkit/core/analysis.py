"""
Hypsometric integral analysis.

Pure-Python port of hypsometric_analysis_v2.py (repo root) using only the csv
module and numpy, so it runs inside any QGIS Python environment (pandas is not
bundled with all QGIS builds).

Input CSVs are the files written by the QGIS Processing algorithm
"Raster terrain analysis - Hypsometric curves" (qgis:hypsometriccurves):
two columns, Area (cumulative, m2 or %) and Elevation (m), one file per
polygon feature, named histogram_<layer>_<fid>.csv.
"""

import csv
import os
from pathlib import Path

import numpy as np

# numpy renamed trapz -> trapezoid in 2.0
_trapezoid = getattr(np, "trapezoid", None) or np.trapz

SUMMARY_FIELDS = [
    "feature_id",
    "file_path",
    "hypsometric_integral_curve",
    "hypsometric_integral_formula",
    "min_elevation",
    "max_elevation",
    "elevation_range",
    "max_area",
    "interpretation",
]


def interpret_hi(hi):
    """Morphological interpretation of a hypsometric integral value."""
    if hi is None:
        return "Undefined - no elevation variation"
    if hi > 0.6:
        return "Youthful - Convex profile, steep slopes"
    if hi > 0.35:
        return "Mature - S-shaped profile, balanced erosion"
    return "Old - Concave profile, advanced erosion"


def feature_id_from_path(csv_path):
    """
    Derive a readable feature id from a histogram CSV filename, using the
    same convention as hypsometric_analysis_v2.py: histogram_X -> Feature_X.
    """
    stem = Path(csv_path).stem
    if stem.startswith("histogram_"):
        stem = "Feature_" + stem[len("histogram_"):]
    return stem or Path(csv_path).stem


def read_hypsometric_csv(csv_path):
    """Read Area/Elevation columns; returns (area, elevation) float arrays."""
    areas = []
    elevations = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("empty file")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        if "area" not in fields or "elevation" not in fields:
            raise ValueError(
                "CSV must contain 'Area' and 'Elevation' columns "
                f"(found: {reader.fieldnames})"
            )
        for row in reader:
            a = row[fields["area"]]
            e = row[fields["elevation"]]
            if a in (None, "") or e in (None, ""):
                continue
            areas.append(float(a))
            elevations.append(float(e))
    if not areas:
        raise ValueError("no data rows")
    return np.asarray(areas, dtype=float), np.asarray(elevations, dtype=float)


def analyze_csv(csv_path):
    """
    Analyze one histogram CSV.

    Returns a dict with the SUMMARY_FIELDS keys plus 'rel_elevation' and
    'rel_area' arrays (for plotting), or raises ValueError on bad input.
    """
    area, elevation = read_hypsometric_csv(csv_path)

    min_elev = float(elevation.min())
    max_elev = float(elevation.max())
    max_area = float(area.max())

    result = {
        "feature_id": feature_id_from_path(csv_path),
        "file_path": str(csv_path),
        "hypsometric_integral_curve": None,
        "hypsometric_integral_formula": None,
        "min_elevation": min_elev,
        "max_elevation": max_elev,
        "elevation_range": max_elev - min_elev,
        "max_area": max_area,
        "interpretation": None,
        "rel_elevation": None,
        "rel_area": None,
    }

    if max_elev == min_elev or max_area == 0:
        result["interpretation"] = interpret_hi(None)
        return result

    # Relative elevation (h/H): 0 at the lowest point, 1 at the highest.
    rel_elevation = (elevation - min_elev) / (max_elev - min_elev)
    # Relative area (a/A): Area is cumulative from the bottom up, so the
    # proportion of area lying above each elevation is 1 - Area/Amax.
    rel_area = 1.0 - (area / max_area)

    hi = float(_trapezoid(rel_area, rel_elevation))

    # HI via the elevation-relief ratio, weighting each elevation bin by the
    # (non-cumulative) area it contributes.
    bin_areas = np.diff(np.append([0.0], area))
    weight_sum = bin_areas.sum()
    if weight_sum > 0:
        mean_elev = float(np.average(elevation, weights=bin_areas))
        result["hypsometric_integral_formula"] = (
            (mean_elev - min_elev) / (max_elev - min_elev)
        )

    result["hypsometric_integral_curve"] = hi
    result["interpretation"] = interpret_hi(hi)
    result["rel_elevation"] = rel_elevation
    result["rel_area"] = rel_area
    return result


def analyze_files(csv_paths):
    """
    Analyze many CSVs. Returns (results, errors) where results is a list of
    analyze_csv() dicts and errors a list of (path, message) tuples.
    """
    results = []
    errors = []
    for path in sorted(csv_paths, key=lambda p: str(p)):
        try:
            results.append(analyze_csv(path))
        except (ValueError, OSError) as exc:
            errors.append((str(path), str(exc)))
    return results, errors


def write_summary_csv(results, out_path):
    """Write the summary table (same columns as hypsometric_analysis_v2.py)."""
    out_path = str(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for res in results:
            writer.writerow(res)
    return out_path
