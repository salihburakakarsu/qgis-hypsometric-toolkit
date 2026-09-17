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
