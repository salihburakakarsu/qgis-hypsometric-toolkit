#!/usr/bin/env python3
"""
Hypsometric integral of each crater under each rim rule.

For every crater in a polygon layer this fits the rim with the plugin's crest
rule and with the slope-threshold rule at each fraction (the same fits as
compare_rim_rules.py), writes one circle per crater and rule to a GeoPackage,
and runs the plugin's own HI analysis on all of them: qgis:hypsometriccurves,
then core.analysis on each histogram. Nothing in the plugin is changed.

It needs QGIS, so run it with the QGIS-bundled Python, e.g. for MacPorts:

    QGIS_APP=/Applications/MacPorts/QGIS3.app /opt/local/bin/python3.14 \\
        dev/hi_by_rim_rule.py DEM.tif CRATERS.gpkg --fid 1 --steps 2 0.5

The circles and the histograms go to dev/test_data/rim_rules/ (git-ignored),
so the circles can be opened in QGIS next to the DEM.
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

from test_plugin import bootstrap_qgis  # noqa: E402  (also sets offscreen Qt)


def write_circles(path, crs_wkt, circles):
    """One polygon per (crater, rule). Feature ids follow the list order."""
    from osgeo import ogr, osr

    if os.path.exists(path):
        os.remove(path)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs_wkt)
    source = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    layer = source.CreateLayer("circles", srs, ogr.wkbPolygon)
    for name, kind in (("crater_fid", ogr.OFTInteger), ("rule", ogr.OFTString),
                       ("diameter_m", ogr.OFTReal), ("d_over_D", ogr.OFTReal)):
        layer.CreateField(ogr.FieldDefn(name, kind))
    for fid, circle in enumerate(circles, start=1):
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in circle["points"] + circle["points"][:1]:
            ring.AddPoint_2D(x, y)
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetFID(fid)
        feature.SetGeometry(polygon)
        feature.SetField("crater_fid", circle["crater_fid"])
        feature.SetField("rule", circle["rule"])
        feature.SetField("diameter_m", circle["diameter_m"])
        if circle["d_over_D"] is not None:
            feature.SetField("d_over_D", circle["d_over_D"])
        layer.CreateFeature(feature)
    source = None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dem", help="DEM raster")
    parser.add_argument("polygons", help="polygon layer around the craters")
    parser.add_argument("--fid", type=int, nargs="+",
                        help="feature id(s) to use (default: all)")
    parser.add_argument("--fractions", type=float, nargs="+",
                        default=[0.10, 0.15, 0.20, 0.30],
                        help="slope fractions (default: 0.1 0.15 0.2 0.3)")
    parser.add_argument("--steps", type=float, nargs="+", default=[2.0],
                        help="elevation step(s) for the HI analysis, in m "
                             "(default: 2)")
    parser.add_argument("--out", default=os.path.join(HERE, "test_data",
                                                      "rim_rules"),
                        help="folder for the circles and histograms")
    args = parser.parse_args()

    prefix = bootstrap_qgis()
    from qgis.core import QgsApplication, QgsRasterLayer, QgsVectorLayer
    QgsApplication.setPrefixPath(prefix, True)
    app = QgsApplication([], False)
    app.initQgis()
    from processing.core.Processing import Processing
    Processing.initialize()

    from osgeo import gdal
    from compare_rim_rules import compare, read_around, read_polygons
    from hypsometric_toolkit.core import analysis, qgis_runner, rim

    # 1. fit every crater under every rule, as compare_rim_rules.py does
    dem_wkt = gdal.Open(args.dem).GetProjection()
    layer_name = os.path.splitext(os.path.basename(args.polygons))[0]
    circles = []
    for crater_fid, wkt, bbox in read_polygons(args.polygons, dem_wkt,
                                               args.fid):
        z, geo = read_around(args.dem, bbox)
        try:
            _fit, _azimuths, _rays, rules = compare(
                z, geo, rim.polygon_mask(geo, wkt), sorted(args.fractions))
        except ValueError as exc:
            print(f"crater {crater_fid}: no rim found ({exc})")
            continue
        for rule in rules:
            circle = rule["circle"]
            if circle is None:
                continue
            centre_x, centre_y = rim.to_map(geo, circle["cx"], circle["cy"])
            radius_m = circle["radius_km"] * 1000.0
            circles.append({
                "crater_fid": crater_fid,
                "rule": rule["name"],
                "diameter_m": 2.0 * radius_m,
                "d_over_D": rule["d_over_D"],
                "points": rim.circle_points(centre_x, centre_y, radius_m),
            })
    if not circles:
        sys.exit("no craters could be fitted")

    os.makedirs(args.out, exist_ok=True)
    gpkg = os.path.join(args.out, f"{layer_name}_rule_circles.gpkg")
    write_circles(gpkg, dem_wkt, circles)

    # 2. the plugin's HI analysis on every circle, at every step
    dem_layer = QgsRasterLayer(args.dem, "dem")
    boundary = QgsVectorLayer(f"{gpkg}|layername=circles", "circles", "ogr")
    if not dem_layer.isValid() or not boundary.isValid():
        sys.exit("QGIS could not open the DEM or the circles")

    hi = {}  # (circle fid, step) -> analysis result
    for step in args.steps:
        out_dir = os.path.join(args.out, f"hi_{layer_name}_step{step:g}")
        shutil.rmtree(out_dir, ignore_errors=True)  # no stale histograms
        os.makedirs(out_dir)
        qgis_runner.run_algorithm(qgis_runner.build_params(
            dem_layer, boundary, False, step, False, out_dir))
        for csv_path in qgis_runner.list_output_csvs(out_dir):
            hi[(analysis.feature_fid_from_path(csv_path), step)] = \
                analysis.analyze_csv(csv_path)

    # 3. one row per crater and rule
    step_heads = "".join(f"  {'HI @' + format(s, 'g') + ' m':>10}"
                         for s in args.steps)
    print(f"\n{'crater':>6}  {'rule':<20} {'D (m)':>6} {'d/D':>6}"
          f"{step_heads}  {'relief (m)':>10}")
    previous = None
    for fid, circle in enumerate(circles, start=1):
        crater = circle["crater_fid"]
        if previous is not None and crater != previous:
            print()
        values = ""
        relief = None
        for step in args.steps:
            result = hi.get((fid, step))
            value = result and result["hypsometric_integral_curve"]
            values += f"  {value:>10.3f}" if value is not None else f"  {'–':>10}"
            if result and relief is None:
                relief = result["elevation_range"]
        d_over_d = (f"{circle['d_over_D']:.3f}"
                    if circle["d_over_D"] is not None else "–")
        print(f"{crater if crater != previous else '':>6}  "
              f"{circle['rule']:<20} {circle['diameter_m']:>6.0f} "
              f"{d_over_d:>6}{values}  "
              f"{relief if relief is not None else float('nan'):>10.1f}")
        previous = crater

    print(f"\nCircles: {gpkg}", flush=True)
    # layers must go before QGIS does, or their destructors segfault on exit
    del boundary, dem_layer
    app.exitQgis()


if __name__ == "__main__":
    main()
