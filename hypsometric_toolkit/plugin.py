"""
Plugin bootstrap: menu entry + toolbar button that open the main dialog.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

PLUGIN_DIR = os.path.dirname(__file__)
MENU_TITLE = "&Hypsometric Analysis Toolkit"


class HypsometricToolkitPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon = QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))
        self.action = QAction(
            icon, "Hypsometric Analysis Toolkit…", self.iface.mainWindow()
        )
        self.action.setToolTip(
            "Run the Hypsometric Curves algorithm and compute hypsometric "
            "integrals"
        )
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(MENU_TITLE, self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginRasterMenu(MENU_TITLE, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None

    def run(self):
        # Import here so a broken dialog import shows up as a plugin error
        # dialog instead of preventing QGIS from loading the plugin at all.
        from .dialog import HypsometricDialog

        if self.dialog is None:
            self.dialog = HypsometricDialog(self.iface,
                                            self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
