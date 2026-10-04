"""
The scratch polygon layer that the dialog's "Draw polygon" tool digitizes into.

The layer is an in-memory ("temporary scratch") layer, so drawing an area of
interest costs nothing and needs no file dialog. QGIS marks it as temporary in
the Layers panel, and it can be kept with Layer > Make Permanent or exported to
a shapefile / GeoPackage like any other layer.

The layer name matters: the Hypsometric Curves algorithm names its output files
`histogram_<layer name>_<feature id>.csv`, so the default avoids spaces and
anything else awkward in a filename.
"""

from qgis.core import QgsFeature, QgsVectorLayer

LAYER_NAME = "Drawn_boundary"


def create_scratch_polygon_layer(crs=None, name=LAYER_NAME):
    """
    An empty in-memory polygon layer to digitize into.

    The layer carries no attribute fields: the algorithm does not need any, and
    it keeps the digitizing tool from having anything to prompt for.
    """
    authid = crs.authid() if crs is not None and crs.isValid() else ""
    uri = f"Polygon?crs={authid}" if authid else "Polygon"
    layer = QgsVectorLayer(uri, name, "memory")
    if layer.isValid() and not authid and crs is not None and crs.isValid():
        # custom CRS without an authority code
        layer.setCrs(crs)
    return layer


def add_polygon(layer, geometry):
    """
    Append a polygon geometry to the scratch layer and refresh it.

    Returns the layer's new feature count, or -1 if the feature was rejected.
    """
    if layer is None or geometry is None or geometry.isNull():
        return -1
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geometry)
    added, _features = layer.dataProvider().addFeatures([feature])
    if not added:
        return -1
    layer.updateExtents()
    layer.triggerRepaint()
    return layer.featureCount()


def clear_polygons(layer):
    """Remove every digitized polygon, keeping the layer itself."""
    if layer is None:
        return False
    provider = layer.dataProvider()
    ids = [feature.id() for feature in layer.getFeatures()]
    if ids:
        provider.deleteFeatures(ids)
    layer.updateExtents()
    layer.triggerRepaint()
    return True


# ------------------------------------------------------- detected rim layer

RIM_LAYER_NAME = "Crater_rim"

# name, kind, key in the detect_rim() result
RIM_FIELDS = [
    ("source", "str", None),
    ("radius_km", "double", "radius_km"),
    ("diameter_km", "double", "diameter_km"),
    ("centre_shift_km", "double", "shift_km"),
    ("fit_rms_km", "double", "fit_rms_km"),
    ("median_rim_km", "double", "median_rim_km"),
    ("rim_std_km", "double", "rim_std_km"),
    ("n_found", "int", "n_found"),
    ("n_rays", "int", "n_rays"),
    ("radius_source", "str", "radius_source"),
    ("quality", "str", "quality"),
]


def _make_field(name, kind):
    """
    QgsField across QGIS versions: QMetaType from 3.38, QVariant before it.
    """
    from qgis.core import QgsField

    try:
        from qgis.PyQt.QtCore import QMetaType
        types = {"str": QMetaType.Type.QString,
                 "double": QMetaType.Type.Double,
                 "int": QMetaType.Type.Int}
        return QgsField(name, types[kind])
    except Exception:
        from qgis.PyQt.QtCore import QVariant
        types = {"str": QVariant.String,
                 "double": QVariant.Double,
                 "int": QVariant.Int}
        return QgsField(name, types[kind])


def create_rim_layer(crs=None, name=RIM_LAYER_NAME):
    """
    In-memory polygon layer for fitted rim circles.

    Carries centre_shift_km and fit_rms_km, the two quality indicators: a large
    shift means the floor centroid was a poor centre, a large rms means the rim
    is not actually circular.
    """
    authid = crs.authid() if crs is not None and crs.isValid() else ""
    uri = f"Polygon?crs={authid}" if authid else "Polygon"
    layer = QgsVectorLayer(uri, name, "memory")
    if not layer.isValid():
        return layer
    if not authid and crs is not None and crs.isValid():
        layer.setCrs(crs)
    layer.dataProvider().addAttributes(
        [_make_field(field_name, kind) for field_name, kind, _key in RIM_FIELDS])
    layer.updateFields()
    return layer


def add_rim_polygon(layer, result, source_label, n_vertices=180):
    """
    Append one fitted rim circle, as a polygon, with its quality attributes.

    Returns the layer's new feature count, or -1 if it was rejected.
    """
    from qgis.core import QgsGeometry, QgsPointXY

    from . import rim as rim_module

    if layer is None or not result:
        return -1

    points = [QgsPointXY(x, y) for x, y in rim_module.circle_points(
        result["centre_x"], result["centre_y"], result["radius_m"], n_vertices)]
    geometry = QgsGeometry.fromPolygonXY([points])

    feature = QgsFeature(layer.fields())
    feature.setGeometry(geometry)
    for field_name, _kind, key in RIM_FIELDS:
        value = source_label if key is None else result.get(key)
        feature.setAttribute(field_name, value)

    added, _features = layer.dataProvider().addFeatures([feature])
    if not added:
        return -1
    layer.updateExtents()
    layer.triggerRepaint()
    return layer.featureCount()
