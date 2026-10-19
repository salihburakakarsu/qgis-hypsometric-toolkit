#!/usr/bin/env python3
"""
Compare the plugin's two rim rules on real craters.

Crest rule (the default, and the scripts' rule): start at the steepest part of
the crater wall and walk outward until the ground stops rising. On a degraded
crater the rim is a soft shoulder with almost flat ground beyond it, so where
the ground "stops rising" is decided by small bumps out on the flat, and the
rim can land too far out.

Slope rule: start at the same steepest point and walk outward until the slope
drops below a fraction of that steepest slope, e.g. 20%, never past the
crest. The fraction is relative to each crater's own wall, so one number
should suit small and large, fresh and degraded craters.

Every rim here is fitted by hypsometric_toolkit.core.rim.detect_rim, exactly as
the plugin's Detect rim button fits it, so the numbers are the plugin's.

Run with any Python that has GDAL, numpy and matplotlib:

    python dev/compare_rim_rules.py DEM.tif CRATERS.gpkg --fid 3

CRATERS is any polygon layer around the craters: a hand-drawn boundary, or a
rim layer the plugin wrote. Leave out --fid to do every polygon. One PNG per
crater goes to dev/test_data/rim_rules/ (git-ignored), and a summary table is
printed.
"""

import argparse
import math
import os
import sys

import numpy as np
from osgeo import gdal, ogr, osr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from hypsometric_toolkit.core import morphometry, rim  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()

# Fractions of the steepest wall slope to try, gentlest first.
FRACTIONS = (0.10, 0.15, 0.20, 0.30)

# Crest in orange; the slope rules as one blue ramp, light = gentlest. Each
# rule also has its own marker, so the plot still reads without colour.
CREST_COLOUR, CREST_MARKER = "#eb6834", "o"
SLOPE_COLOURS = ("#86b6ef", "#5598e7", "#2a78d6", "#184f95")
SLOPE_MARKERS = ("s", "v", "^", "D")
INK, INK_MUTED, RAY_GREY, SURFACE = "#0b0b0b", "#52514e", "#d4d3cd", "#fcfcfb"


# ----------------------------------------------------------------- reading

def read_polygons(path, dem_wkt, fids=None):
    """(fid, wkt, bbox) for each polygon, in the DEM's CRS."""
    source = ogr.Open(path)  # keep a reference, or the layer dies with it
    layer = source.GetLayer(0)
    transform = None
    layer_srs = layer.GetSpatialRef()
    if layer_srs is not None and dem_wkt:
        dem_srs = osr.SpatialReference()
        dem_srs.ImportFromWkt(dem_wkt)
        for srs in (layer_srs, dem_srs):
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not layer_srs.IsSame(dem_srs):
            transform = osr.CoordinateTransformation(layer_srs, dem_srs)

    polygons = []
    for feature in layer:
        if fids and feature.GetFID() not in fids:
            continue
        geometry = feature.GetGeometryRef().Clone()
        if transform is not None:
            geometry.Transform(transform)
        xmin, xmax, ymin, ymax = geometry.GetEnvelope()
        polygons.append((feature.GetFID(), geometry.ExportToWkt(),
                         (xmin, ymin, xmax, ymax)))
    return polygons


def read_around(dem_path, bbox):
    """Read the DEM around one polygon, at the resolution the plugin picks."""
    gt = gdal.Open(dem_path).GetGeoTransform()
    pixel = max(abs(gt[1]), abs(gt[5]))
    window = rim.search_window(bbox)
    span_px = min(bbox[2] - bbox[0], bbox[3] - bbox[1]) / pixel
    downsample = rim.downsample_for_feature(
        (window[2] - window[0]) / pixel, (window[3] - window[1]) / pixel,
        span_px)
    return rim.read_dem(dem_path, downsample, window=window)


# ------------------------------------------------------------------- crater

def compare(z, geo, mask, fractions):
    """
    Fit the rim with the crest rule, then the slope rule at each fraction.

    Returns one dict per rule: its detect_rim() result as `fit` (None if
    nothing was found), the rim pick on each azimuth in map coordinates, and
    depth and d/D as the Results tab would report them. Raises ValueError if
    even the crest rule finds no rim.
    """
    specs = [(rim.RIM_CREST, None, CREST_COLOUR, CREST_MARKER)]
    specs += [(rim.RIM_SLOPE, fraction, SLOPE_COLOURS[i], SLOPE_MARKERS[i])
              for i, fraction in enumerate(fractions)]

    rules = []
    for rule, fraction, colour, marker in specs:
        args = {"rim_rule": rule}
        if fraction is not None:
            args["slope_fraction"] = fraction
        try:
            fit = rim.detect_rim(z, geo, mask=mask, **args)
        except ValueError:
            if rule == rim.RIM_CREST:
                raise
            fit = None

        picks, depth_m, d_over_d = [], None, None
        if fit is not None:
            picks = [rim.rim_point(fit["centre_x"], fit["centre_y"],
                                   row["azimuth"], row["rim_km"])
                     if row["rim_km"] is not None else None
                     for row in fit["rows"]]
            try:
                measured = morphometry.compute(z, geo, fit)
                depth_m = measured["depth_rim_to_floor_m"]
                d_over_d = measured["d_over_D"]
            except ValueError:
                pass
        rules.append({"name": rim.rim_rule_label(rule, fraction),
                      "fraction": fraction, "fit": fit, "picks": picks,
                      "depth_m": depth_m, "d_over_D": d_over_d,
                      "colour": colour, "marker": marker})
    return rules


# ----------------------------------------------------------------- plotting

def ray_slope(z, cy, cx, azimuth, px, py, cap_km, smooth):
    """
    Smoothed elevation and slope along one ray, sampled as rim.ray_rim does.

    Returns (dist_km, elevation_m, slope_m_per_km), or None when the ray is
    too short to use.
    """
    theta = math.radians(azimuth)
    dx, dy = math.sin(theta), -math.cos(theta)
    step_km = math.hypot(dx * px, dy * py) / 1000.0
    n = int(cap_km / step_km)
    if n < rim.MIN_RAY_SAMPLES:
        return None

    s = np.arange(n)
    yy = np.clip((cy + dy * s).astype(int), 0, z.shape[0] - 1)
    xx = np.clip((cx + dx * s).astype(int), 0, z.shape[1] - 1)
    prof = z[yy, xx]
    bad = np.flatnonzero(~np.isfinite(prof))
    if bad.size:
        if bad[0] < rim.MIN_RAY_SAMPLES:
            return None
        prof, s = prof[:bad[0]], s[:bad[0]]

    pad = smooth // 2
    prof = np.convolve(np.pad(prof, pad, mode="edge"),
                       np.ones(smooth) / smooth, mode="valid")
    dist = s * step_km
    return dist, prof, np.gradient(prof, np.maximum(dist, 1e-9))


def steepest_wall(dist, slope):
    """Index of the steepest point, skipping the inner 15% like the plugin."""
    inner = dist > 0.15 * dist.max()
    if inner.sum() < 5:
        return None
    return int(np.argmax(np.where(inner, slope, -np.inf)))


def line_profile(z, cy, cx, azimuth, px, py, half_km):
    """Raw elevation along a line through the centre: back side, then front."""
    theta = math.radians(azimuth)
    dx, dy = math.sin(theta), -math.cos(theta)
    step_km = math.hypot(dx * px, dy * py) / 1000.0
    n = int(half_km / step_km)
    s = np.arange(-n, n + 1)
    yy = np.clip((cy + dy * s).astype(int), 0, z.shape[0] - 1)
    xx = np.clip((cx + dx * s).astype(int), 0, z.shape[1] - 1)
    return (s + n) * step_km * 1000.0, z[yy, xx], n * step_km * 1000.0


def plot_crater(z, geo, rules, profile_azimuth, title, out_path):
    """Profile, slope and map panels, all centred on the crest rule's centre."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource

    crest = rules[0]["fit"]
    px, py = geo["px"], geo["py"]
    cy, cx = crest["cy"], crest["cx"]
    x0, y0 = crest["centre_x"], crest["centre_y"]
    fitted = [rule for rule in rules if rule["fit"] is not None]
    half_km = min(1.5 * max(r["fit"]["radius_km"] for r in fitted),
                  crest["cap_km"])
    azimuths = [row["azimuth"] for row in crest["rows"]]

    fig, (ax_prof, ax_slope, ax_map) = plt.subplots(
        1, 3, figsize=(17, 5.2), gridspec_kw={"width_ratios": [1.5, 1.1, 1]},
        facecolor=SURFACE)
    for ax in (ax_prof, ax_slope, ax_map):
        ax.set_facecolor(SURFACE)
        ax.tick_params(colors=INK_MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(RAY_GREY)

    # 1. rim-to-rim profile through the centre, like QGIS's profile tool. Each
    # rule's picks on this azimuth and the opposite one are projected onto the
    # line, since a rule's own centre can sit a few metres from the crest's.
    step = 360.0 / len(azimuths)
    front = int(round(profile_azimuth / step)) % len(azimuths)
    back = (front + len(azimuths) // 2) % len(azimuths)
    along = math.radians(azimuths[front])
    x_m, elev, centre_m = line_profile(z, cy, cx, azimuths[front], px, py,
                                       half_km)
    ax_prof.plot(x_m, elev, color=INK, linewidth=1.2)
    for rule in fitted:
        for index in (back, front):
            pick = rule["picks"][index]
            if pick is None:
                continue
            x = centre_m + ((pick[0] - x0) * math.sin(along)
                            + (pick[1] - y0) * math.cos(along))
            # the crest is drawn larger, so it still shows as a ring when a
            # slope pick lands on the same spot
            size = 14 if rule is rules[0] else 8
            ax_prof.plot(x, np.interp(x, x_m, elev), linestyle="none",
                         marker=rule["marker"], markersize=size,
                         markerfacecolor=rule["colour"],
                         markeredgecolor=SURFACE, markeredgewidth=1.5)
    ax_prof.set_title(f"Profile through the centre, "
                      f"{azimuths[back]:.0f}° → {azimuths[front]:.0f}°",
                      color=INK, fontsize=10, loc="left")
    ax_prof.set_xlabel("distance (m)", color=INK_MUTED, fontsize=9)
    ax_prof.set_ylabel("elevation (m)", color=INK_MUTED, fontsize=9)
    ax_prof.grid(color=RAY_GREY, linewidth=0.5, alpha=0.6)

    # 2. slope against distance on every ray: why each rule stops where it does
    rays = [ray_slope(z, cy, cx, az, px, py, crest["cap_km"],
                      rim.DEFAULTS["smooth"]) for az in azimuths]
    wall_slopes = []
    for ray in rays:
        if ray is None:
            continue
        dist, _elev, slope = ray
        ax_slope.plot(dist * 1000, np.degrees(np.arctan(slope / 1000.0)),
                      color=RAY_GREY, linewidth=0.7)
        k_wall = steepest_wall(dist, slope)
        if k_wall is not None:
            wall_slopes.append(slope[k_wall])
    used = [ray for ray in rays if ray is not None]
    if used:
        shortest = min(len(ray[0]) for ray in used)
        median = np.median([ray[2][:shortest] for ray in used], axis=0)
        ax_slope.plot(used[0][0][:shortest] * 1000,
                      np.degrees(np.arctan(median / 1000.0)), color=INK,
                      linewidth=1.6)
    if wall_slopes:
        typical_wall = float(np.median(wall_slopes))
        for rule in rules[1:]:
            level = np.degrees(np.arctan(rule["fraction"] * typical_wall
                                         / 1000.0))
            ax_slope.axhline(level, color=rule["colour"], linewidth=1,
                             linestyle="--")
            ax_slope.annotate(f"{rule['fraction']:.0%}", (1.0, level),
                              xycoords=("axes fraction", "data"),
                              xytext=(3, 0), textcoords="offset points",
                              va="center", fontsize=8, color=INK_MUTED)
    for rule in fitted:
        ax_slope.axvline(rule["fit"]["radius_m"], color=rule["colour"],
                         linewidth=1.5)
    ax_slope.axhline(0, color=INK_MUTED, linewidth=0.8)
    ax_slope.set_xlim(0, half_km * 1000)
    ax_slope.set_title("Slope outward along each ray", color=INK,
                       fontsize=10, loc="left")
    ax_slope.set_xlabel("distance from centre (m)", color=INK_MUTED,
                        fontsize=9)
    ax_slope.set_ylabel("slope (°, + = rising outward)", color=INK_MUTED,
                        fontsize=9)
    ax_slope.grid(color=RAY_GREY, linewidth=0.5, alpha=0.6)

    # 3. map view: hillshade with each rule's fitted circle and picks
    half_px = int(math.ceil(half_km * 1000 / min(px, py))) + 1
    r0, r1 = max(int(cy) - half_px, 0), min(int(cy) + half_px, z.shape[0] - 1)
    c0, c1 = max(int(cx) - half_px, 0), min(int(cx) + half_px, z.shape[1] - 1)
    patch = z[r0:r1 + 1, c0:c1 + 1]
    patch = np.where(np.isfinite(patch), patch, np.nanmean(patch))
    shade = LightSource(azdeg=315, altdeg=45).hillshade(patch, dx=px, dy=py)
    ax_map.imshow(shade, cmap="gray", vmin=-0.4, vmax=1.2,
                  extent=((c0 - cx) * px, (c1 + 1 - cx) * px,
                          (cy - r1 - 1) * py, (cy - r0) * py))
    theta = np.linspace(0, 2 * np.pi, 361)
    for rule in fitted:
        fit = rule["fit"]
        ox, oy = fit["centre_x"] - x0, fit["centre_y"] - y0
        ax_map.plot(ox + fit["radius_m"] * np.sin(theta),
                    oy + fit["radius_m"] * np.cos(theta),
                    color=rule["colour"], linewidth=1.8)
        for pick in rule["picks"]:
            if pick is not None:
                ax_map.plot(pick[0] - x0, pick[1] - y0, linestyle="none",
                            marker=rule["marker"], markersize=5,
                            markerfacecolor=rule["colour"],
                            markeredgecolor=SURFACE, markeredgewidth=0.8)
    ax_map.set_xlim(-half_km * 1000, half_km * 1000)
    ax_map.set_ylim(-half_km * 1000, half_km * 1000)
    ax_map.set_aspect("equal")
    ax_map.set_title("Map view (north up)", color=INK, fontsize=10,
                     loc="left")
    ax_map.set_xlabel("m east of centre", color=INK_MUTED, fontsize=9)

    # one legend for all three panels, carrying each rule's numbers
    handles = []
    for rule in rules:
        fit = rule["fit"]
        if fit is None:
            text = f"{rule['name']}: no rim found"
        else:
            text = (f"{rule['name']}:  D = {fit['diameter_km'] * 1000:.0f} m"
                    f",  d/D = {_fmt(rule['d_over_D'], '.3f')}"
                    f",  {fit['n_found']}/{fit['n_rays']} rays")
        handles.append(plt.Line2D([], [], color=rule["colour"], linewidth=1.8,
                                  marker=rule["marker"], markersize=8,
                                  markeredgecolor=SURFACE, label=text))
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False,
               fontsize=9, labelcolor=INK)
    fig.suptitle(title, color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.12, 1, 0.95))
    fig.savefig(out_path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def _fmt(value, spec):
    return "–" if value is None else format(value, spec)


# --------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dem", help="DEM raster")
    parser.add_argument("polygons", help="polygon layer around the craters")
    parser.add_argument("--fid", type=int, nargs="+",
                        help="feature id(s) to compare (default: all)")
    parser.add_argument("--fractions", type=float, nargs="+",
                        default=list(FRACTIONS),
                        help="slope fractions to try, at most 4 "
                             "(default: 0.1 0.15 0.2 0.3)")
    parser.add_argument("--azimuth", type=float, default=90.0,
                        help="direction of the plotted profile, degrees "
                             "clockwise from north (default 90: west → east)")
    parser.add_argument("--out", default=os.path.join(HERE, "test_data",
                                                      "rim_rules"),
                        help="folder for the PNGs")
    args = parser.parse_args()
    if not 1 <= len(args.fractions) <= len(SLOPE_COLOURS):
        parser.error(f"give 1 to {len(SLOPE_COLOURS)} fractions")
    fractions = sorted(args.fractions)

    os.makedirs(args.out, exist_ok=True)
    dem_wkt = gdal.Open(args.dem).GetProjection()
    layer_name = os.path.splitext(os.path.basename(args.polygons))[0]
    polygons = read_polygons(args.polygons, dem_wkt, args.fid)
    if not polygons:
        sys.exit("no matching polygons in the layer")

    print(f"{'fid':>4}  {'rule':<10} {'D (m)':>7} {'vs crest':>9} "
          f"{'depth (m)':>10} {'d/D':>7} {'rays':>6}")
    for fid, wkt, bbox in polygons:
        z, geo = read_around(args.dem, bbox)
        mask = rim.polygon_mask(geo, wkt)
        try:
            rules = compare(z, geo, mask, fractions)
        except ValueError as exc:
            print(f"{fid:>4}  no rim found: {exc}")
            continue

        crest_d = rules[0]["fit"]["diameter_km"] * 1000
        for i, rule in enumerate(rules):
            fit = rule["fit"]
            d_m = fit["diameter_km"] * 1000 if fit else None
            change = (f"{(d_m - crest_d) / crest_d:+.0%}"
                      if d_m is not None and i else "")
            rays = f"{fit['n_found']}/{fit['n_rays']}" if fit else "–"
            print(f"{fid if i == 0 else '':>4}  {rule['name']:<10} "
                  f"{_fmt(d_m, '.0f'):>7} {change:>9} "
                  f"{_fmt(rule['depth_m'], '.1f'):>10} "
                  f"{_fmt(rule['d_over_D'], '.3f'):>7} {rays:>6}")

        title = (f"{layer_name}, fid {fid}  ·  {os.path.basename(args.dem)} "
                 f"at {geo['px']:.1f} m pixels  ·  crest fit: "
                 f"{rules[0]['fit']['quality']}")
        out_path = os.path.join(args.out, f"{layer_name}_fid{fid}.png")
        plot_crater(z, geo, rules, args.azimuth, title, out_path)
    print(f"\nPlots written to {args.out}")


if __name__ == "__main__":
    main()
