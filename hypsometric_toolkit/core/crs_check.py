"""
Sanity checks on the DEM's CRS, and on the elevation step.

Scope matters here, and it is narrower than it first appears. The Hypsometric
Curves algorithm reprojects the boundary features into the *raster's* CRS and
takes areas from the raster's own geotransform, so the project CRS does not
affect anything this plugin reports — verified by running the same analysis
under three unrelated project CRSs and getting identical areas. What does
affect the numbers is the DEM's own CRS.

The other thing worth knowing: the hypsometric integral is a ratio of areas, so
a uniform scale error cancels out and HI survives it untouched. Absolute areas
do not. These checks therefore aim to say precisely which column to distrust,
rather than to cast doubt on the whole result.
"""

import math
import re

AREAS_OK = "ok"


def _proj_number(proj_string, key):
    match = re.search(r"\+%s=(-?[0-9.]+)" % key, proj_string or "")
    return float(match.group(1)) if match else None


def _projection_name(proj_string):
    match = re.search(r"\+proj=(\w+)", proj_string or "")
    return match.group(1) if match else None


def body_radius(crs):
    """Semi-major axis of the CRS's body, in metres, or None."""
    if crs is None or not crs.isValid():
        return None
    proj_string = crs.toProj()
    for key in ("R", "a"):
        value = _proj_number(proj_string, key)
        if value:
            return value
    try:
        from qgis.core import QgsEllipsoidUtils
        params = QgsEllipsoidUtils.ellipsoidParameters(crs.ellipsoidAcronym())
        if params.valid and params.semiMajor > 0:
            return float(params.semiMajor)
    except Exception:  # noqa: BLE001 - the acronym may be empty or unknown
        pass
    return None


def inspect_dem_crs(dem_layer, project_crs=None):
    """
    Check the DEM's CRS for things that would make its areas wrong.

    Returns {"warnings": [str], "areas_reliable": bool, "area_reliability": str}.
    """
    warnings = []
    areas_reliable = True
    reliability = AREAS_OK

    crs = dem_layer.crs() if dem_layer is not None else None
    if crs is None or not crs.isValid():
        return {
            "warnings": ["The DEM has no valid CRS, so its areas have no units. "
                         "HI is a ratio and is unaffected."],
            "areas_reliable": False,
            "area_reliability": "no CRS",
        }

    proj_string = crs.toProj()
    label = crs.authid() or _projection_name(proj_string) or "custom"

    if crs.isGeographic():
        areas_reliable = False
        reliability = "degrees, not m2"
        warnings.append(
            f"The DEM is in a geographic CRS ({label}), so its pixels are "
            "sized in degrees: the Area column is in square degrees, not "
            "square metres, and the elevation step is the only metric input. "
            "HI is a ratio of areas and is unaffected. Reproject the DEM to a "
            "projected CRS if you need real areas."
        )

    # The project CRS does not change these numbers, but a body mismatch means
    # the project is set up for a different world, so anything measured on
    # screen or by another tool will be wrong.
    dem_radius = body_radius(crs)
    project_radius = body_radius(project_crs)
    if dem_radius and project_radius:
        ratio = max(dem_radius, project_radius) / min(dem_radius, project_radius)
        if ratio > 1.01:
            warnings.append(
                f"The DEM's CRS uses a body radius of {dem_radius:,.0f} m while "
                f"the project uses {project_radius:,.0f} m — a factor of "
                f"{ratio:.2f}. This plugin measures in the DEM's CRS, so the "
                "results below are unaffected, but on-screen measurements and "
                "other tools will be wrong until the project CRS matches the "
                "body."
            )

    # Equirectangular pixels are not equal-area: their ground area scales as
    # cos(lat)/cos(lat_ts). Report the size of the effect rather than silently
    # correcting it.
    if _projection_name(proj_string) == "eqc" and dem_radius:
        lat_ts = _proj_number(proj_string, "lat_ts") or 0.0
        extent = dem_layer.extent()
        centre_lat = math.degrees(extent.center().y() / dem_radius)
        top_lat = math.degrees(extent.yMaximum() / dem_radius)
        bottom_lat = math.degrees(extent.yMinimum() / dem_radius)
        try:
            scale = math.cos(math.radians(centre_lat)) / math.cos(math.radians(lat_ts))
            spread = abs(math.cos(math.radians(top_lat))
                         - math.cos(math.radians(bottom_lat))) / math.cos(math.radians(lat_ts))
        except ZeroDivisionError:
            scale = spread = None
        if scale and abs(scale - 1.0) > 0.01:
            warnings.append(
                f"The DEM is equirectangular with lat_ts={lat_ts:g}°, but its "
                f"data sits near {centre_lat:.1f}°. Pixel ground area there is "
                f"{scale:.3f}x the nominal value, so absolute areas carry that "
                "bias. HI is a ratio and is affected only by the variation "
                f"across the raster, about {abs(spread) * 100:.2f}%."
            )

    return {
        "warnings": warnings,
        "areas_reliable": areas_reliable,
        "area_reliability": reliability,
    }


def hi_gap_warning(result, step):
    """
    Flag a feature whose elevation step is too coarse for its relief.

    The two HI estimates are identical for continuous topography and diverge on
    binned data, with the elevation-relief ratio biased high by about
    step / (2 x relief) because each bin's area is credited to the bin's
    boundary elevation. A gap much larger than that, or a predicted bias that is
    itself large, means the step is too coarse for this feature.
    """
    curve = result.get("hypsometric_integral_curve")
    formula = result.get("hypsometric_integral_formula")
    relief = result.get("elevation_range")
    if curve is None or formula is None or not relief or not step:
        return None
    expected = step / (2.0 * relief)
    if expected < 0.02:
        return None
    return (
        f"{result['feature_id']}: HI (curve) {curve:.3f} vs HI (formula) "
        f"{formula:.3f}; with a {step:g} step over {relief:,.0f} m of relief the "
        f"binning alone explains about {expected:.3f}. Reduce the elevation "
        "step for this feature if the two need to agree."
    )
