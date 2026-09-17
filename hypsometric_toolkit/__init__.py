def classFactory(iface):
    from .plugin import HypsometricToolkitPlugin

    return HypsometricToolkitPlugin(iface)
