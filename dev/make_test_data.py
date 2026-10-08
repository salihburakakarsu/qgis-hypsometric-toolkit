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


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "./test_data"
    dem_path, gpkg_path = generate(outdir)
    print(f"Wrote {dem_path}")
    print(f"Wrote {gpkg_path} ({len(LANDFORMS)} polygons)")


if __name__ == "__main__":
    main()
