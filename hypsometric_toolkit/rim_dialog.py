"""
Parameter dialog for rim detection (the "Detect rim…" button).

Defaults match the standalone morphometry scripts, so a rim fitted here and one
fitted there are the same measurement.
"""

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
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

SHAPE_CIRCLE = "circle"
SHAPE_TRACED = "traced"
SHAPE_BOTH = "both"


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
            "the rim, and a least-squares circle is refit a few times."
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

        shape_group = QGroupBox("Rim outline")
        shape_layout = QVBoxLayout(shape_group)
        self.circle_radio = QRadioButton(
            "Fitted circle — least-squares circle through the rim picks")
        self.traced_radio = QRadioButton(
            "Traced rim — connect the rim picks themselves")
        self.both_radio = QRadioButton("Both, in one layer")
        self.circle_radio.setChecked(True)
        for widget in (self.circle_radio, self.traced_radio, self.both_radio):
            shape_layout.addWidget(widget)
        shape_hint = QLabel(
            "Craters are often not circular. The circle gives one diameter and "
            "a residual saying how circular the rim is; the traced outline "
            "keeps the real shape, so its area is not forced to a circle."
        )
        shape_hint.setWordWrap(True)
        shape_layout.addWidget(shape_hint)
        layout.addWidget(shape_group)

        params = QGroupBox("Detection")
        form = QFormLayout(params)

        # two compact rows rather than a group of their own: the dialog is
        # already close to a laptop screen's height
        self.rule_combo = QComboBox()
        self.rule_combo.addItem("Crest — where the ground stops rising",
                                rim.RIM_CREST)
        self.rule_combo.addItem("Slope break — where the wall flattens out",
                                rim.RIM_SLOPE)
        self.rule_combo.setCurrentIndex(
            self.rule_combo.findData(rim.DEFAULTS["rim_rule"]))
        self.rule_combo.setToolTip(
            "Where on each ray the rim is picked. The crest is the scripts' "
            "rule and what most published diameters use. A degraded crater's "
            "rim is a soft shoulder on nearly flat ground, so its crest lands "
            "too far out, on the flat; the slope break stops where the wall "
            "has flattened out, never past the crest, and gives a smaller "
            "diameter."
        )
        form.addRow("Rim at:", self.rule_combo)

        self.fraction_spin = QDoubleSpinBox()
        self.fraction_spin.setRange(1.0, 90.0)
        self.fraction_spin.setDecimals(0)
        self.fraction_spin.setSingleStep(5.0)
        self.fraction_spin.setValue(rim.DEFAULTS["slope_fraction"] * 100.0)
        self.fraction_spin.setSuffix(" % of the steepest wall slope")
        self.fraction_spin.setToolTip(
            "Slope break only: the rim is put where the slope first drops "
            "below this share of the wall's steepest slope. Higher pulls the "
            "rim further in.")
        self.rule_combo.currentIndexChanged.connect(self._update_fraction)
        self._update_fraction()
        form.addRow("Flat below:", self.fraction_spin)

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

        self.auto_downsample_check = QCheckBox(
            "Choose automatically from the polygon size")
        self.auto_downsample_check.setChecked(True)
        self.auto_downsample_check.setToolTip(
            "A small crater in a large DTM needs a finer resolution than the "
            "raster's size alone suggests. Only applies when seeding from "
            "polygons."
        )
        self.auto_downsample_check.toggled.connect(
            lambda on: self.downsample_spin.setEnabled(not on))
        self.downsample_spin.setEnabled(False)
        form.addRow("", self.auto_downsample_check)

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

    def _update_fraction(self):
        """The fraction only means something for the slope break."""
        self.fraction_spin.setEnabled(
            self.rule_combo.currentData() == rim.RIM_SLOPE)

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
        if self.both_radio.isChecked():
            shape = SHAPE_BOTH
        elif self.traced_radio.isChecked():
            shape = SHAPE_TRACED
        else:
            shape = SHAPE_CIRCLE
        return {
            "shape": shape,
            "auto_downsample": self.auto_downsample_check.isChecked(),
            "mode": MODE_POLYGON if self.polygon_radio.isChecked() else MODE_WHOLE,
            "n_azimuths": self.rays_spin.value(),
            "passes": self.passes_spin.value(),
            "floor_pct": self.floor_spin.value(),
            "downsample": self.downsample_spin.value(),
            "rim_rule": self.rule_combo.currentData(),
            "slope_fraction": self.fraction_spin.value() / 100.0,
        }
