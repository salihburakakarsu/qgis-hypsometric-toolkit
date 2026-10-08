"""
Crater depth, diameter and d/D, reported alongside the hypsometric integral.

HI on its own cannot say whether the hypsometric curve carries information
beyond standard morphometry, so the summary CSV also carries the depth/diameter
ratio the comparison rests on.

Ported from `crater_metrics.py` of the lunar morphometry scripts, including its
floor definition and its defaults, so the plugin and those scripts report the
same numbers.

On "depth": several different quantities get called that. This module reports
each separately rather than picking one —

    rim_elev_m                   rim crest elevation, from the radial profile
    floor_elev_m                 crater floor, by the chosen definition
    depth_rim_to_floor_m         rim crest to floor: the crater's depth
    depth_alt_floor_m            the same, using the other floor definition
    rim_above_surroundings_m     rim crest above the surrounding plain
    floor_below_surroundings_m   floor below the surrounding plain

`elevation_range` in the HI columns is none of these: it is max minus min
inside the polygon, set by two single pixels.
"""

import math

import numpy as np

from . import rim as rim_module

DEFAULTS = {
    # Mean inside this fraction of the rim radius is very sensitive to the
    # fraction itself, because it starts averaging in the wall.
    "floor_frac": 0.4,
    # A low percentile over a wider disc ignores wall pixels and barely moves
    # when the radius changes, so it is the default.
    "wide_floor_frac": 0.6,
    "floor_q": 5.0,
    "floor_method": "percentile",
    "radial_step_km": 0.2,
    # The rim elevation is read off a radial bin. A bin fixed at 0.2 km
    # spans 40% of a 1 km crater's radius and smears the crest away, so
    # the bin is scaled to the crater - never coarser than the scripts'
    # default, so large craters are measured exactly as before.
    "radial_bins_per_radius": 25,
    "reference_inner": 1.5,
    "reference_outer": 2.2,
}

# appended to the summary CSV, after the HI columns
FIELDS = [
    "diameter_km",
    "rim_elev_m",
    "floor_elev_m",
    "depth_rim_to_floor_m",
    "depth_alt_floor_m",
    "floor_method",
    "d_over_D",
    "rim_above_surroundings_m",
    "floor_below_surroundings_m",
    "pike1977_predicted_depth_m",
    "pike_branch",
    "centre_shift_km",
    "rim_fit_rms_km",
    "rays_found",
    "rim_confidence",
]


def pike1977_depth_km(diameter_km):
    """
    Pike (1977) lunar depth-diameter relation.

    The simple and complex branches disagree badly across the transition near
    15 km, so the branch used is reported with the value.
    """
    if diameter_km < 15.0:
        return 0.196 * diameter_km ** 1.010, "simple"
    return 1.044 * diameter_km ** 0.301, "complex"


def _finite(values):
    values = np.asarray(values)
    return values[np.isfinite(values)]


def compute(z, geo, fit, floor_frac=None, floor_q=None, floor_method=None,
            radial_step_km=None):
    """
    Depth, diameter and d/D for one fitted rim.

    `fit` is a detect_rim() result: it supplies the refined centre, the rim
    radius, and the fit quality that is carried through to the summary.
    """
    floor_frac = DEFAULTS["floor_frac"] if floor_frac is None else floor_frac
    floor_q = DEFAULTS["floor_q"] if floor_q is None else floor_q
    floor_method = (DEFAULTS["floor_method"] if floor_method is None
                    else floor_method)
    explicit_step = radial_step_km is not None
    step = (DEFAULTS["radial_step_km"] if radial_step_km is None
            else radial_step_km)

    rim_km = fit.get("radius_km")
    if not rim_km:
        raise ValueError("the rim radius is unknown, so depth cannot be measured")

    # The fit's cy/cx are pixels on whatever grid it was measured on, which is
    # not necessarily this one, so go back through the map coordinates.
    cx, cy = rim_module.from_map(geo, fit["centre_x"], fit["centre_y"])
    if not (0 <= cx < z.shape[1] and 0 <= cy < z.shape[0]):
        raise ValueError("the fitted centre falls outside this raster")
    px, py = geo["px"], geo["py"]

    if not explicit_step:
        scaled = rim_km / DEFAULTS["radial_bins_per_radius"]
        floor = 2.0 * max(px, py) / 1000.0
        step = max(min(step, scaled), floor)

    r, centres, mean = rim_module.radial_profile(
        z, cy, cx, px, py, step, rim_module.inscribed_radius(z, cy, cx, px, py))
    if not len(centres) or np.all(np.isnan(mean)):
        raise ValueError("the radial profile is empty; the crater may be too "
                         "close to the raster edge")

    k = int(np.nanargmin(np.abs(centres - rim_km)))
    rim_z = float(mean[k])
    diameter_km = 2.0 * rim_km

    # Two floor definitions, both reported so the sensitivity stays visible.
    inner = _finite(z[r < rim_km * floor_frac])
    wide = _finite(z[r < rim_km * DEFAULTS["wide_floor_frac"]])
    floor_mean = float(inner.mean()) if inner.size else float("nan")
    floor_pctl = float(np.percentile(wide, floor_q)) if wide.size else float("nan")

    if floor_method == "percentile":
        floor_z, floor_alt = floor_pctl, floor_mean
    else:
        floor_z, floor_alt = floor_mean, floor_pctl

    if not math.isfinite(floor_z):
        raise ValueError("no valid elevations inside the crater floor")

    reference = ((r > rim_km * DEFAULTS["reference_inner"])
                 & (r < rim_km * DEFAULTS["reference_outer"])
                 & np.isfinite(z))
    reference_z = (float(z[reference].mean()) if reference.sum() > 100
                   else float("nan"))

    depth = rim_z - floor_z
    pike_km, branch = pike1977_depth_km(diameter_km)

    def rounded(value, digits):
        return round(value, digits) if math.isfinite(value) else None

    return {
        "diameter_km": round(diameter_km, 2),
        "rim_elev_m": rounded(rim_z, 1),
        "floor_elev_m": rounded(floor_z, 1),
        "depth_rim_to_floor_m": rounded(depth, 1),
        "depth_alt_floor_m": rounded(rim_z - floor_alt, 1),
        "floor_method": floor_method,
        "d_over_D": rounded(depth / (diameter_km * 1000.0), 4),
        "rim_above_surroundings_m": rounded(rim_z - reference_z, 1),
        "floor_below_surroundings_m": rounded(reference_z - floor_z, 1),
        "pike1977_predicted_depth_m": round(pike_km * 1000.0, 1),
        "pike_branch": branch,
        "centre_shift_km": round(fit["shift_km"], 2),
        "rim_fit_rms_km": (round(fit["fit_rms_km"], 3)
                           if fit.get("fit_rms_km") is not None else None),
        "rays_found": f"{fit['n_found']}/{fit['n_rays']}",
        "rim_confidence": fit.get("quality"),
    }
