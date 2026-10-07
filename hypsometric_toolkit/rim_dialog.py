"""
Parameter dialog for rim detection (the "Detect rim…" button).

Defaults match the standalone morphometry scripts, so a rim fitted here and one
fitted there are the same measurement.
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from .core import rim

MODE_WHOLE = "whole"
MODE_POLYGON = "polygon"


class RimOptionsDialog(QDialog):
    def __init__(self, parent=None, has_polygons=True, dem_size=None,
                 pixel_size=None):
        super().__init__(parent)
        self.setWindowTitle("Detect crater rim")
        self.setMinimumWidth(460)
        self._dem_size = dem_size
        self._pixel_size = pixel_size

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Finds the crater centre and fits a circle to its rim: a floor "
            "centroid seeds the search, rays are cast on each azimuth to pick "
            "the rim crest, and a least-squares circle is refit a few times."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        mode_group = QGroupBox("Where to look")
        mode_layout = QVBoxLayout(mode_group)
        self.whole_radio = QRadioButton(
            "Whole DEM — finds the deepest feature, one crater per raster")
        self.polygon_radio = QRadioButton(
            "Inside the boundary polygons — one rim per polygon")
        self.polygon_radio.setChecked(has_polygons)
        self.whole_radio.setChecked(not has_polygons)
        self.polygon_radio.setEnabled(has_polygons)
        mode_layout.addWidget(self.polygon_radio)
        mode_layout.addWidget(self.whole_radio)
        hint = QLabel(
            "With two comparable depressions in one DEM the whole-DEM seed "
            "lands between them, in neither crater; seeding from a polygon "
            "avoids that."
        )
        hint.setWordWrap(True)
        mode_layout.addWidget(hint)
        layout.addWidget(mode_group)

        params = QGroupBox("Detection")
        form = QFormLayout(params)

        self.rays_spin = QSpinBox()
        self.rays_spin.setRange(8, 360)
        self.rays_spin.setValue(rim.DEFAULTS["n_azimuths"])
        self.rays_spin.setToolTip("Azimuths on which the rim crest is picked")
        form.addRow("Rays:", self.rays_spin)

        self.passes_spin = QSpinBox()
        self.passes_spin.setRange(0, 10)
        self.passes_spin.setValue(rim.DEFAULTS["passes"])
        self.passes_spin.setToolTip("Circle-fit refinement passes")
        form.addRow("Refinement passes:", self.passes_spin)

        self.floor_spin = QDoubleSpinBox()
        self.floor_spin.setRange(0.1, 50.0)
        self.floor_spin.setDecimals(1)
        self.floor_spin.setSingleStep(0.5)
        self.floor_spin.setValue(rim.DEFAULTS["floor_pct"])
        self.floor_spin.setSuffix(" %")
        self.floor_spin.setToolTip(
            "Lowest fraction of pixels whose centroid seeds the search")
        form.addRow("Floor percentile:", self.floor_spin)

        self.downsample_spin = QSpinBox()
        self.downsample_spin.setToolTip(
            "Read the DEM at 1/N resolution. Higher is faster, but each ray "
            "needs at least 30 samples, so too high a value finds nothing. "
            "The default is scaled to this raster."
        )
        if dem_size:
            suggested = rim.suggested_downsample(*dem_size)
            self.downsample_spin.setRange(1, max(suggested, 1))
            self.downsample_spin.setValue(suggested)
        else:
            self.downsample_spin.setRange(1, 64)
            self.downsample_spin.setValue(rim.DEFAULTS["downsample"])
        self.downsample_spin.valueChanged.connect(self._update_grid_label)
        form.addRow("Downsample:", self.downsample_spin)

        self.grid_label = QLabel()
        self.grid_label.setWordWrap(True)
        form.addRow("", self.grid_label)
        self._update_grid_label()
        layout.addWidget(params)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Detect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_grid_label(self):
        """Show what the chosen downsample actually leaves to work with."""
        if not self._dem_size:
            self.grid_label.setText("")
            return
        factor = self.downsample_spin.value()
        width = int(self._dem_size[0]) // factor
        height = int(self._dem_size[1]) // factor
        text = f"reads {width} x {height} pixels"
        if self._pixel_size:
            text += (f", {self._pixel_size[0] * factor:.1f} x "
                     f"{self._pixel_size[1] * factor:.1f} map units each")
        if min(width, height) < rim.MIN_SEARCH_SPAN_PX:
            text += " — may be too coarse for the rays to find a rim"
        self.grid_label.setText(text)

    def values(self):
        return {
            "mode": MODE_POLYGON if self.polygon_radio.isChecked() else MODE_WHOLE,
            "n_azimuths": self.rays_spin.value(),
            "passes": self.passes_spin.value(),
            "floor_pct": self.floor_spin.value(),
            "downsample": self.downsample_spin.value(),
        }
