"""
Crater rim detection: find a crater's centre and fit a circle to its rim,
so the boundary polygon can be generated from the DEM instead of drawn by hand.

Ported from `crater_core.py` of the lunar morphometry scripts, keeping the same
algorithm and defaults so the plugin and those scripts agree:

    floor centroid (a seed, not the centre)
      -> cast rays on N azimuths, pick the rim crest on each
      -> least-squares circle through the picks
      -> re-cast from the fitted centre, repeat

The floor centroid is only a starting guess: for an asymmetric floor it is not
the geometric centre of the rim, and the refinement matters (one crater in the
thesis set moved 4.1 km, which was 2 km of diameter and 1 km of depth).

The maths is numpy only. Rasters are read with GDAL rather than rasterio, since
QGIS always ships GDAL (the Hypsometric Curves algorithm itself imports it) but
does not ship rasterio.
"""

import math

import numpy as np

# A ray shorter than this many samples is rejected, so the search area has
# to be a few times this across in downsampled pixels for anything to be
# found. This is what makes an over-aggressive downsample fail.
MIN_RAY_SAMPLES = 30
MIN_SEARCH_SPAN_PX = 120

# Script defaults, kept identical so results are comparable.
DEFAULTS = {
    "downsample": 12,
    "n_azimuths": 24,
    "passes": 3,
    "floor_pct": 2.0,
    "smooth": 9,
    "min_rays": 12,
    "max_rms_frac": 0.08,
}


# ----------------------------------------------------------------- loading

def read_dem(path, downsample=1, band=1):
    """
    Read a DEM at reduced resolution.

    Returns (z, geo): z is float64 with nodata as NaN, geo describes the
    downsampled grid (pixel size, origin, geotransform, CRS WKT).
    """
    from osgeo import gdal

    downsample = max(int(downsample), 1)
    dataset = gdal.Open(str(path))
    if dataset is None:
        raise ValueError(f"could not open raster: {path}")

    raster_band = dataset.GetRasterBand(band)
    width = dataset.RasterXSize // downsample
    height = dataset.RasterYSize // downsample
    if width < 2 or height < 2:
        raise ValueError("downsample factor is larger than the raster")

    resample = getattr(gdal, "GRIORA_Average", None)
    if resample is not None:
        z = raster_band.ReadAsArray(buf_xsize=width, buf_ysize=height,
                                    resample_alg=resample)
    else:  # very old GDAL
        z = raster_band.ReadAsArray(buf_xsize=width, buf_ysize=height)

    z = np.asarray(z, dtype="float64")
    z[z < -1e30] = np.nan
    nodata = raster_band.GetNoDataValue()
    if nodata is not None:
        z[z == nodata] = np.nan

    gt = dataset.GetGeoTransform()
    px = abs(gt[1]) * downsample
    py = abs(gt[5]) * downsample
    geo = {
        "px": px,
        "py": py,
        "origin_x": gt[0],
        "origin_y": gt[3],
        "gt": (gt[0], px, 0.0, gt[3], 0.0, -py),
        "crs_wkt": dataset.GetProjection(),
        "width": width,
        "height": height,
        "downsample": downsample,
    }
    dataset = None
    return z, geo


def suggested_downsample(width, height, cap=None):
    """
    A downsample factor that leaves enough pixels for the ray search.

    The scripts' default of 12 suits LROC NAC DTMs of tens of thousands of
    pixels; on a small raster it leaves a grid too coarse for any ray to reach
    MIN_RAY_SAMPLES, and nothing is found. Scale it to the raster instead.
    """
    cap = DEFAULTS["downsample"] if cap is None else cap
    smallest = max(min(int(width), int(height)), 1)
    return max(1, min(int(cap), smallest // MIN_SEARCH_SPAN_PX))


def search_span_px(z, mask=None):
    """How many pixels across the region the rays may travel through."""
    if mask is None:
        return min(z.shape)
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return 0
    height = int(np.flatnonzero(rows)[-1] - np.flatnonzero(rows)[0] + 1)
    width = int(np.flatnonzero(cols)[-1] - np.flatnonzero(cols)[0] + 1)
    return min(height, width)


def polygon_mask(geo, wkt):
    """
    Boolean array, True inside the polygon, on the downsampled grid.

    `wkt` must already be in the raster's CRS.
    """
    from osgeo import gdal, ogr, osr

    mem = gdal.GetDriverByName("MEM").Create(
        "", geo["width"], geo["height"], 1, gdal.GDT_Byte)
    mem.SetGeoTransform(geo["gt"])
    if geo["crs_wkt"]:
        mem.SetProjection(geo["crs_wkt"])

    # the vector memory driver was renamed "MEM" in GDAL 3.11
    driver = ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")
    vector = driver.CreateDataSource("mask")
    srs = None
    if geo["crs_wkt"]:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(geo["crs_wkt"])
    layer = vector.CreateLayer("poly", srs, ogr.wkbUnknown)  # multipolygons too
    geometry = ogr.CreateGeometryFromWkt(wkt)
    if geometry is None:
        raise ValueError("could not parse the polygon geometry")
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
    feature = None

    gdal.RasterizeLayer(mem, [1], layer, burn_values=[1])
    mask = mem.GetRasterBand(1).ReadAsArray().astype(bool)
    mem = None
    vector = None
    return mask


def to_map(geo, col, row):
    """Pixel (col, row) on the downsampled grid -> map coordinates."""
    return (geo["origin_x"] + col * geo["px"],
            geo["origin_y"] - row * geo["py"])


def from_map(geo, x, y):
    """
    Map coordinates -> pixel (col, row) on the downsampled grid.

    The inverse of to_map. Pixel coordinates are only meaningful for the grid
    they were measured on, so anything reusing a fit on a different downsample
    has to come back through the map coordinates.
    """
    return ((x - geo["origin_x"]) / geo["px"],
            (geo["origin_y"] - y) / geo["py"])


# ------------------------------------------------------------ centre & rim

def floor_centroid(z, pct=2.0):
    """
    Centroid of the lowest `pct` percent of pixels.

    A starting guess only: for an asymmetric floor this is not the geometric
    centre of the rim.
    """
    if not np.any(np.isfinite(z)):
        raise ValueError("the raster has no valid pixels")
    threshold = np.nanpercentile(z, pct)
    rows, cols = np.where(np.nan_to_num(z, nan=1e30) <= threshold)
    if rows.size == 0:
        raise ValueError("no pixels below the floor percentile")
    return float(rows.mean()), float(cols.mean())


def inscribed_radius(z, cy, cx, px, py):
    """Largest radius whose full circle is still inside the raster (km)."""
    return min(cx * px, (z.shape[1] - cx) * px,
               cy * py, (z.shape[0] - cy) * py) / 1000.0


def ray_rim(z, cy, cx, dy, dx, px, py, cap_km, smooth=9):
    """
    Rim crest along one ray: the first turnover outward of the steepest wall.

    Returns (rim_dist_km, rim_elev_m, reach_km), or (None, reason, reach_km).
    """
    step_km = math.hypot(dx * px, dy * py) / 1000.0
    if step_km <= 0:
        return None, "degenerate step", 0.0
    n = int(cap_km / step_km)
    if n < 30:
        return None, "too short", cap_km

    s = np.arange(n)
    yy = np.clip((cy + dy * s).astype(int), 0, z.shape[0] - 1)
    xx = np.clip((cx + dx * s).astype(int), 0, z.shape[1] - 1)
    prof = z[yy, xx]

    bad = np.where(~np.isfinite(prof))[0]
    if bad.size:
        n = int(bad[0])
        if n < 30:
            return None, "nodata near centre", 0.0
        prof, s = prof[:n], s[:n]

    reach = float(s[-1] * step_km)

    # Edge-pad before smoothing. np.convolve(mode="same") zero-pads, and with
    # elevations near -3000 m that fabricates a ~3 km cliff at both ends of
    # every ray, which then wins the steepest-gradient search.
    pad = smooth // 2
    prof = np.convolve(np.pad(prof, pad, mode="edge"),
                       np.ones(smooth) / smooth, mode="valid")
    dist = s * step_km

    gradient = np.gradient(prof, np.maximum(dist, 1e-9))
    inner = dist > 0.15 * dist.max()
    if inner.sum() < 5:
        return None, "too short", reach

    k_wall = int(np.argmax(np.where(inner, gradient, -np.inf)))
    for i in range(k_wall, len(gradient) - 1):
        if gradient[i] >= 0 > gradient[i + 1]:
            return float(dist[i]), float(prof[i]), reach
    return None, "no crest within reach", reach


def cast_all(z, cy, cx, px, py, cap_km, n_az, smooth=9):
    """Rim pick on every azimuth. 0 degrees = north, increasing clockwise."""
    rows, points = [], []
    for az in np.arange(0, 360, 360 / n_az):
        theta = math.radians(float(az))
        dx, dy = math.sin(theta), -math.cos(theta)
        r_km, z_m, reach = ray_rim(z, cy, cx, dy, dx, px, py, cap_km, smooth)
        rows.append({"azimuth": float(az), "rim_km": r_km, "rim_z": z_m,
                     "reach_km": reach})
        if r_km is not None:
            points.append((cx + dx * r_km * 1000 / px,
                           cy + dy * r_km * 1000 / py))
    return rows, np.array(points)


def fit_circle(points, px, py):
    """Least-squares circle through rim picks. Returns (cx, cy, R_m, rms_m)."""
    x = points[:, 0] * px
    y = points[:, 1] * py
    a = np.c_[2 * x, 2 * y, np.ones(len(x))]
    cx_m, cy_m, k = np.linalg.lstsq(a, x ** 2 + y ** 2, rcond=None)[0]
    radius = math.sqrt(max(k + cx_m ** 2 + cy_m ** 2, 0.0))
    rms = float(np.std(np.hypot(x - cx_m, y - cy_m) - radius))
    return cx_m / px, cy_m / py, radius, rms


def refine_centre(z, px, py, n_az=24, passes=3, floor_pct=2.0, cap_km=None,
                  smooth=9, seed=None):
    """
    Floor centroid, then iterated circle fits to the rim picks.

    A large `shift_km` means the floor centroid was a poor centre; a large
    `fit_rms_km` means the rim is not circular (oblique impact, or a bad
    detection).
    """
    cy, cx = seed if seed is not None else floor_centroid(z, floor_pct)
    cy0, cx0 = cy, cx

    radius = rms = None
    for _ in range(max(int(passes), 0)):
        cap = cap_km or inscribed_radius(z, cy, cx, px, py)
        _rows, points = cast_all(z, cy, cx, px, py, cap, n_az, smooth)
        if len(points) < 5:
            break
        cx, cy, radius, rms = fit_circle(points, px, py)

    cap = cap_km or inscribed_radius(z, cy, cx, px, py)
    rows, _points = cast_all(z, cy, cx, px, py, cap, n_az, smooth)
    radii = [row["rim_km"] for row in rows if row["rim_km"] is not None]

    return {
        "cy": cy, "cx": cx,
        "cy0": cy0, "cx0": cx0,
        "shift_km": float(math.hypot((cx - cx0) * px, (cy - cy0) * py) / 1000.0),
        "fit_R_km": (radius / 1000.0) if radius else None,
        "fit_rms_km": (rms / 1000.0) if rms is not None else None,
        "median_rim_km": float(np.median(radii)) if radii else None,
        "rim_std_km": float(np.std(radii)) if radii else None,
        "n_rays": int(n_az),
        "n_found": len(radii),
        "cap_km": cap,
        "rows": rows,
    }


def quality_flag(fit, rim_km, min_rays=12, max_rms_frac=0.08):
    """Same confidence rule as the standalone scripts."""
    if fit["n_found"] < min_rays:
        return f"LOW CONFIDENCE ({fit['n_found']}/{fit['n_rays']} rays)"
    rms = fit["fit_rms_km"]
    if rms is not None and rim_km and rms > max_rms_frac * rim_km:
        return f"NON-CIRCULAR (rms {rms:.2f} km)"
    return "ok"


# ---------------------------------------------------------------- detection

def polygon_area_m2(points):
    """Shoelace area of a closed ring given as (x, y) map coordinates."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def interpolated_radii(rows):
    """
    Rim radius on every azimuth, filling in the rays that found nothing.

    Missing azimuths are interpolated from their neighbours around the circle,
    so the traced outline stays closed. Returns (radii_km, n_interpolated), or
    (None, 0) when too few rays succeeded to interpolate between.
    """
    azimuths = np.array([row["azimuth"] for row in rows], dtype=float)
    radii = np.array([row["rim_km"] if row["rim_km"] is not None else np.nan
                      for row in rows], dtype=float)
    found = np.isfinite(radii)
    if found.sum() < 3:
        return None, 0
    if found.all():
        return radii, 0

    # wrap the found samples either side so azimuth 0 interpolates across north
    az_found = azimuths[found]
    r_found = radii[found]
    az_extended = np.concatenate([az_found - 360.0, az_found, az_found + 360.0])
    r_extended = np.concatenate([r_found, r_found, r_found])
    return np.interp(azimuths, az_extended, r_extended), int((~found).sum())


def rim_point(centre_x, centre_y, azimuth_deg, radius_km):
    """Map coordinates of a rim pick. 0 degrees = north, increasing clockwise."""
    theta = math.radians(azimuth_deg)
    return (centre_x + radius_km * 1000.0 * math.sin(theta),
            centre_y + radius_km * 1000.0 * math.cos(theta))


def detect_rim(z, geo, mask=None, n_azimuths=DEFAULTS["n_azimuths"],
               passes=DEFAULTS["passes"], floor_pct=DEFAULTS["floor_pct"],
               min_rays=DEFAULTS["min_rays"],
               max_rms_frac=DEFAULTS["max_rms_frac"],
               smooth=DEFAULTS["smooth"]):
    """
    Find the crater centre and fit its rim circle.

    With `mask`, only pixels inside it are considered: the seed comes from the
    masked region and rays stop at its edge, so one crater can be targeted in a
    DEM holding several. Without it the deepest feature in the whole raster is
    found, which is the standalone script's behaviour.

    Returns a dict with the fit plus map-space centre and radius, or raises
    ValueError when no rim could be found.
    """
    # A polygon selects which crater to measure, by seeding the search inside
    # it. The rays themselves run on the whole raster: confining them to the
    # polygon truncates every profile at its edge, and a polygon drawn round a
    # crater ends at the rim - exactly where the crest's outward turnover is,
    # so no crest is found and the centre never refines.
    seed = None
    if mask is not None:
        if mask.shape != z.shape:
            raise ValueError("mask shape does not match the raster")
        if not mask.any():
            raise ValueError("the polygon covers no raster pixels")
        inside = np.where(mask, z, np.nan)
        if not np.any(np.isfinite(inside)):
            raise ValueError("the polygon covers no valid elevation data")
        seed = floor_centroid(inside, floor_pct)

    fit = refine_centre(z, geo["px"], geo["py"], n_az=n_azimuths,
                        passes=passes, floor_pct=floor_pct, smooth=smooth,
                        seed=seed)

    rim_km = fit["fit_R_km"] or fit["median_rim_km"]
    if not rim_km:
        reasons = {}
        for row in fit["rows"]:
            if row["rim_km"] is None:
                reasons[row["rim_z"]] = reasons.get(row["rim_z"], 0) + 1
        detail = ", ".join(f"{count} {why}"
                           for why, count in sorted(reasons.items()))
        message = f"no rim crest was found on any azimuth ({detail})."
        span = search_span_px(z, None)
        if reasons.get("too short") or reasons.get("nodata near centre"):
            message += (
                f" The search area is about {span} pixels across at this "
                f"downsample, and each ray needs {MIN_RAY_SAMPLES} samples. "
                "Lower the downsample factor."
            )
        else:
            message += (" Try more rays, or a smaller downsample factor, so "
                        "the rim crest is resolved.")
        raise ValueError(message)

    centre_x, centre_y = to_map(geo, fit["cx"], fit["cy"])
    seed_x, seed_y = to_map(geo, fit["cx0"], fit["cy0"])

    # The circle is a model; the picks themselves are the measurement. Tracing
    # them follows a non-circular rim, which a single fitted radius cannot.
    radii, n_interpolated = interpolated_radii(fit["rows"])
    traced = []
    if radii is not None:
        traced = [rim_point(centre_x, centre_y, row["azimuth"], radius)
                  for row, radius in zip(fit["rows"], radii)]
    traced_area = polygon_area_m2(traced) if len(traced) >= 3 else None

    result = dict(fit)
    result.update({
        "traced_points": traced,
        "n_interpolated": n_interpolated,
        "traced_area_km2": (traced_area / 1e6) if traced_area else None,
        "traced_eq_radius_km": (math.sqrt(traced_area / math.pi) / 1000.0
                                if traced_area else None),
        "circle_area_km2": math.pi * rim_km ** 2,
        "centre_x": centre_x,
        "centre_y": centre_y,
        "seed_x": seed_x,
        "seed_y": seed_y,
        "radius_km": rim_km,
        "radius_m": rim_km * 1000.0,
        "diameter_km": rim_km * 2.0,
        "radius_source": "circle fit" if fit["fit_R_km"] else "ray median",
        "quality": quality_flag(fit, rim_km, min_rays, max_rms_frac),
    })
    return result


def radial_distance_km(z, cy, cx, px, py):
    """Distance in km from (cy, cx) to every pixel."""
    ny, nx = z.shape
    rows, cols = np.mgrid[0:ny, 0:nx]
    return np.hypot((rows - cy) * py, (cols - cx) * px) / 1000.0


def radial_profile(z, cy, cx, px, py, step_km, r_max_km, min_px=40):
    """
    Azimuthally averaged elevation against radius.

    Returns (r, bin_centres_km, mean_elevation), with NaN for bins holding
    fewer than min_px valid pixels. Ported from the standalone scripts so the
    rim elevation is read off the same profile they use.
    """
    r = radial_distance_km(z, cy, cx, px, py)
    edges = np.arange(0.0, r_max_km, step_km)
    centres = (edges[:-1] + edges[1:]) / 2.0
    mean = np.full(centres.shape, np.nan)
    for i in range(len(centres)):
        inside = (r >= edges[i]) & (r < edges[i + 1]) & np.isfinite(z)
        if inside.sum() > min_px:
            mean[i] = z[inside].mean()
    return r, centres, mean


def circle_points(centre_x, centre_y, radius_m, n=180):
    """Vertices approximating a circle, for building the output polygon."""
    return [(centre_x + radius_m * math.cos(2 * math.pi * i / n),
             centre_y + radius_m * math.sin(2 * math.pi * i / n))
            for i in range(n)]
