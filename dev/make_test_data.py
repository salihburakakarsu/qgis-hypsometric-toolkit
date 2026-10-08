#!/usr/bin/env python3
"""
Generate synthetic test data for the Hypsometric Analysis Toolkit plugin.

Creates in the output directory:
  - test_dem.tif    : 300x300 GeoTIFF, EPSG:32719, two gaussian hills and a
                      crater (rim ring + bowl) on a 2900 m plateau
  - boundaries.gpkg : polygon layer "features" with one square per landform

Run with any Python that has GDAL + numpy — most conveniently the one bundled
with QGIS:

    /Applications/QGIS-LTR.app/Contents/MacOS/bin/python3 dev/make_test_data.py out/
"""

import math
import os
import sys

import numpy as np
from osgeo import gdal, ogr, osr

EPSG = 32719
PIXEL = 10.0            # metres
NX = NY = 300           # 3 km x 3 km
ORIGIN_X, ORIGIN_Y = 500000.0, 7300000.0
BASE_ELEVATION = 2900.0

# name, centre x, centre y (metres from the raster origin), half-width
LANDFORMS = [
    ("steep_hill", 750, 2250, 500),
    ("broad_hill", 2200, 2100, 800),
    ("crater", 1500, 800, 550),
]


def build_dem_array():
    """Elevation array (row 0 = south edge), as float64."""
    xs = np.arange(NX) * PIXEL
    ys = np.arange(NY) * PIXEL
    x, y = np.meshgrid(xs, ys)

    def gauss(cx, cy, amp, sigma):
        return amp * np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2)))

    dem = np.full((NY, NX), BASE_ELEVATION)
    # steep, narrow hill
    dem += gauss(750, 2250, 180.0, 180.0)
    # broad, gentle hill
    dem += gauss(2200, 2100, 120.0, 400.0)
    # crater: raised rim ring around a depressed bowl
    r = np.sqrt((x - 1500) ** 2 + (y - 800) ** 2)
    dem += 60.0 * np.exp(-((r - 350.0) ** 2) / (2 * 90.0 ** 2))
    dem += -80.0 * np.exp(-(r ** 2) / (2 * 220.0 ** 2))
    return dem


def generate(outdir):
    """Write test_dem.tif and boundaries.gpkg into outdir; return their paths."""
    os.makedirs(outdir, exist_ok=True)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)

    dem_path = os.path.join(outdir, "test_dem.tif")
    ds = gdal.GetDriverByName("GTiff").Create(dem_path, NX, NY, 1,
                                              gdal.GDT_Float32)
    # north-up geotransform, so the array is flipped to put north in row 0
    ds.SetGeoTransform((ORIGIN_X, PIXEL, 0, ORIGIN_Y + NY * PIXEL, 0, -PIXEL))
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(
        np.flipud(build_dem_array()).astype(np.float32))
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds.FlushCache()
    ds = None

    gpkg_path = os.path.join(outdir, "boundaries.gpkg")
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)
    vds = ogr.GetDriverByName("GPKG").CreateDataSource(gpkg_path)
    layer = vds.CreateLayer("features", srs, ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
    for name, cx, cy, half in LANDFORMS:
        mx, my = ORIGIN_X + cx, ORIGIN_Y + cy
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for dx, dy in [(-half, -half), (half, -half), (half, half),
                       (-half, half), (-half, -half)]:
            # AddPoint_2D matters: 2.5D vertices make the Hypsometric Curves
            # algorithm fail to rasterize the boundary ("OGR Error: Corrupt data")
            ring.AddPoint_2D(mx + dx, my + dy)
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(ring)
        feat = ogr.Feature(layer.GetLayerDefn())
        feat.SetGeometry(poly)
        feat.SetField("name", name)
        layer.CreateFeature(feat)
        feat = None
    vds = None

    return dem_path, gpkg_path


def generate_two_craters(outdir, name="two_craters.tif"):
    """
    DEM holding two craters of comparable depth.

    This is the failure mode the handoff describes: a seed taken from the
    lowest pixels of the whole raster lands between the two depressions, in
    neither crater, so the rim has to be seeded from a polygon instead.
    Returns (path, crater_a, crater_b) with each crater as (x, y, rim_radius).
    """
    nx = ny = 400
    pixel = 20.0
    origin_x, origin_y = 500000.0, 7300000.0
    craters = [(2000.0, 2000.0, 1200.0, 800.0),
               (6000.0, 6000.0, 1200.0, 780.0)]

    xs = np.arange(nx) * pixel
    ys = np.arange(ny) * pixel
    x, y = np.meshgrid(xs, ys)

    dem = np.full((ny, nx), BASE_ELEVATION)
    for cx, cy, rim_r, depth in craters:
        r = np.hypot(x - cx, y - cy)
        dem -= depth * np.exp(-(r / (rim_r * 0.7)) ** 2)
        dem += 300.0 * np.exp(-((r - rim_r) / (rim_r * 0.2)) ** 2)

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((origin_x, pixel, 0, origin_y + ny * pixel, 0, -pixel))
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(np.flipud(dem).astype(np.float32))
    ds.FlushCache()
    ds = None

    def to_map(cx, cy, rim_r):
        return (origin_x + cx, origin_y + cy, rim_r)

    return path, to_map(*craters[0][:3]), to_map(*craters[1][:3])


def generate_elliptical_crater(outdir, name="elliptical_crater.tif",
                               a=1800.0, b=900.0):
    """
    DEM with one clearly elliptical crater, like an oblique impact.

    A fitted circle cannot represent this: it returns one diameter and a large
    residual. Tracing the rim picks keeps the real outline, which is the point
    of the traced mode. Returns (path, centre_x, centre_y, a, b).
    """
    nx = ny = 400
    pixel = 20.0
    origin_x, origin_y = 500000.0, 7300000.0
    cx, cy = 4000.0, 4000.0

    xs = np.arange(nx) * pixel
    ys = np.arange(ny) * pixel
    x, y = np.meshgrid(xs, ys)

    # elliptical radius: 1.0 on the rim crest
    rho = np.sqrt(((x - cx) / a) ** 2 + ((y - cy) / b) ** 2)
    dem = np.full((ny, nx), BASE_ELEVATION)
    dem -= 800.0 * np.exp(-(rho / 0.7) ** 2)
    dem += 300.0 * np.exp(-((rho - 1.0) / 0.2) ** 2)

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((origin_x, pixel, 0, origin_y + ny * pixel, 0, -pixel))
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(np.flipud(dem).astype(np.float32))
    ds.FlushCache()
    ds = None
    return path, origin_x + cx, origin_y + cy, a, b


def generate_mixed_craters(outdir, name="mixed_craters.tif"):
    """
    DEM with one large crater and one small one far apart.

    The small crater is the case where a ray cap taken from the whole raster
    fails: the steepest-wall scan ignores the inner 15% of each ray, which on a
    raster-sized cap is further out than the small crater's rim.
    Returns (path, big, small), each as (x, y, rim_radius).
    """
    nx = ny = 800
    pixel = 20.0
    origin_x, origin_y = 500000.0, 7300000.0
    craters = [(4000.0, 11000.0, 3500.0, 900.0),
               (11000.0, 4000.0, 700.0, 400.0)]

    xs = np.arange(nx) * pixel
    ys = np.arange(ny) * pixel
    x, y = np.meshgrid(xs, ys)

    dem = np.full((ny, nx), BASE_ELEVATION)
    for cx, cy, rim_r, depth in craters:
        r = np.hypot(x - cx, y - cy)
        dem -= depth * np.exp(-(r / (rim_r * 0.7)) ** 2)
        dem += depth * 0.35 * np.exp(-((r - rim_r) / (rim_r * 0.2)) ** 2)

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, name)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds = gdal.GetDriverByName("GTiff").Create(path, nx, ny, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((origin_x, pixel, 0, origin_y + ny * pixel, 0, -pixel))
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(np.flipud(dem).astype(np.float32))
    ds.FlushCache()
    ds = None

    def to_map(cx, cy, rim_r, _depth):
        return (origin_x + cx, origin_y + cy, rim_r)

    return path, to_map(*craters[0]), to_map(*craters[1])


# ---------------------------------------------------------- lunar craters

MOON_RADIUS = 1737400.0
MOON_PROJ = (f"+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +R={MOON_RADIUS} "
             "+units=m +no_defs")
# Pike (1977) splits the lunar simple/complex branches near 15 km.
LUNAR_TRANSITION_KM = 15.0


def pike_depth_km(diameter_km):
    """Pike (1977) rim-to-floor depth for a fresh lunar crater."""
    if diameter_km < LUNAR_TRANSITION_KM:
        return 0.196 * diameter_km ** 1.010, "simple"
    return 1.044 * diameter_km ** 0.301, "complex"


def pike_rim_height_km(diameter_km):
    """Pike (1977) rim crest height above the surrounding plain."""
    if diameter_km < LUNAR_TRANSITION_KM:
        return 0.036 * diameter_km ** 1.014
    return 0.236 * diameter_km ** 0.399


def pike_floor_diameter_km(diameter_km):
    """Pike (1977) flat-floor diameter of a complex crater."""
    return 0.187 * diameter_km ** 1.249


def generate_lunar_crater(outdir, diameter_km, pixel=None, name=None,
                          plain=-1000.0, central_peak_fraction=0.33):
    """
    One synthetic lunar crater whose morphology follows Pike (1977).

    Built from the published depth, rim height and floor diameter rather than
    from arbitrary gaussians, so its depth/diameter ratio is what the
    literature expects for that size: about 0.20 for a simple crater, falling
    with size once complex.

    Simple craters are parabolic bowls. Complex craters get a flat floor, a
    terraced wall and a central peak. Both carry an ejecta blanket decaying as
    r^-3 outside the rim, and sit in the Moon's equirectangular frame.

    Returns (path, info) where info records the ground truth.
    """
    radius = diameter_km * 1000.0 / 2.0
    depth_km, kind = pike_depth_km(diameter_km)
    depth = depth_km * 1000.0
    rim_height = pike_rim_height_km(diameter_km) * 1000.0
    rim_z = plain + rim_height
    floor_z = rim_z - depth

    pixel = pixel or max(radius / 100.0, 1.0)
    half = radius * 3.2
    n = int(2 * half / pixel)
    axis = (np.arange(n) + 0.5) * pixel - half
    x, y = np.meshgrid(axis, axis)
    r = np.hypot(x, y)
    z = np.full(r.shape, plain, dtype=float)

    if kind == "simple":
        bowl = r <= radius
        z[bowl] = floor_z + (rim_z - floor_z) * (r[bowl] / radius) ** 2
    else:
        floor_radius = pike_floor_diameter_km(diameter_km) * 1000.0 / 2.0
        wall = (r > floor_radius) & (r <= radius)
        span = (r[wall] - floor_radius) / (radius - floor_radius)
        # A monotone staircase: terraces are flat benches, so they do not add
        # local maxima that a rim detector could mistake for the crest.
        steps, amplitude = 4.0, 0.75
        benched = span - (amplitude / (2 * math.pi * steps)) * np.sin(
            2 * math.pi * steps * span)
        benched = (benched - benched.min()) / (benched.max() - benched.min())
        z[wall] = floor_z + (rim_z - floor_z) * benched ** 1.4
        z[r <= floor_radius] = floor_z
        peak_radius = floor_radius * 0.30
        z += ((central_peak_fraction * depth)
              * np.exp(-(r / peak_radius) ** 2) * (r <= floor_radius))

    outside = r > radius
    z[outside] = plain + rim_height * (radius / r[outside]) ** 3

    os.makedirs(outdir, exist_ok=True)
    name = name or f"lunar_{diameter_km:g}km_{kind}.tif"
    path = os.path.join(outdir, name)
    srs = osr.SpatialReference()
    srs.ImportFromProj4(MOON_PROJ)
    ds = gdal.GetDriverByName("GTiff").Create(path, n, n, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((-half, pixel, 0, half, 0, -pixel))
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(z.astype(np.float32))
    ds.FlushCache()
    ds = None

    return path, {
        "diameter_km": diameter_km,
        "kind": kind,
        "depth_m": depth,
        "d_over_D": depth_km / diameter_km,
        "rim_height_m": rim_height,
        "rim_elev_m": rim_z,
        "floor_elev_m": floor_z,
        "centre_x": 0.0,
        "centre_y": 0.0,
        "radius_m": radius,
        "pixel_m": pixel,
    }


LUNAR_CRATERS = ((1.0, 5.0), (5.0, 20.0), (25.0, 50.0))


def generate_lunar_craters(outdir):
    """The three reference craters: 1 km and 5 km simple, 25 km complex."""
    return [generate_lunar_crater(outdir, diameter, pixel)
            for diameter, pixel in LUNAR_CRATERS]


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "./test_data"
    dem_path, gpkg_path = generate(outdir)
    print(f"Wrote {dem_path}")
    print(f"Wrote {gpkg_path} ({len(LANDFORMS)} polygons)")


if __name__ == "__main__":
    main()
