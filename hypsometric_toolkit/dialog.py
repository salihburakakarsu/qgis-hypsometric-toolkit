"""
Main dialog of the Hypsometric Analysis Toolkit plugin.

UI is built in code (no .ui file) so the plugin runs straight from a zip.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)
from qgis.core import (QgsCoordinateTransform, QgsGeometry, QgsMapLayerProxyModel,
                       QgsProcessingFeedback, QgsProject)
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox

from .core import (analysis, drawing, morphometry, plotting,
                   qgis_runner, rim)
from .rim_dialog import (MODE_POLYGON, MODE_WHOLE, SHAPE_BOTH,
                         SHAPE_CIRCLE, SHAPE_TRACED, RimOptionsDialog)

RESULT_COLUMNS = [
    ("feature_id", "Feature"),
    ("hypsometric_integral_curve", "HI (curve)"),
    ("hypsometric_integral_formula", "HI (formula)"),
    ("min_elevation", "Min elev."),
    ("max_elevation", "Max elev."),
    ("elevation_range", "Relief"),
    ("max_area", "Max area"),
    ("interpretation", "Interpretation"),
]


class _DialogFeedback(QgsProcessingFeedback):
    """Feedback that mirrors algorithm messages into the dialog log."""

    def __init__(self, log_func):
        super().__init__()
        self._log = log_func

    def pushInfo(self, info):
        self._log(info)
        super().pushInfo(info)

    def reportError(self, error, fatalError=False):
        self._log(f"ERROR: {error}")
        super().reportError(error, fatalError)


class HypsometricDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Hypsometric Analysis Toolkit")
        self.setMinimumSize(780, 680)

        self._feedback = None
        self._running = False
        self._results = []
        self._output_dir = None
        self._plot_path = None
        self._draw_tool = None
        self._drawn_layer = None
        self._rim_fits = {}
        self._last_dem = None
        self._last_boundary = None
        self._map_tool_watched = False

        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        main = QVBoxLayout(self)

        # --- inputs
        input_group = QGroupBox("Input")
        form = QFormLayout(input_group)

        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow("DEM raster:", self.dem_combo)

        self.boundary_combo = QgsMapLayerComboBox()
        self.boundary_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.draw_btn = QPushButton("Draw polygon")
        self.draw_btn.setCheckable(True)
        self.draw_btn.setToolTip(
            "Digitize an area of interest straight onto the map canvas, into a "
            "temporary polygon layer that is then used as the boundary"
        )
        self.draw_btn.toggled.connect(self._on_draw_toggled)
        self.rim_btn = QPushButton("Detect rim…")
        self.rim_btn.setToolTip(
            "Fit a crater rim circle from the DEM and use it as the "
            "boundary, instead of drawing one by hand"
        )
        self.rim_btn.clicked.connect(self._on_detect_rim_clicked)
        boundary_row = QHBoxLayout()
        boundary_row.addWidget(self.boundary_combo, 1)
        boundary_row.addWidget(self.draw_btn)
        boundary_row.addWidget(self.rim_btn)
        form.addRow("Boundary polygons:", boundary_row)

        self.selected_only_check = QCheckBox("Selected features only")
        form.addRow("", self.selected_only_check)

        self.step_spin = QDoubleSpinBox()
        self.step_spin.setRange(0.01, 100000.0)
        self.step_spin.setDecimals(2)
        self.step_spin.setValue(10.0)
        self.step_spin.setSuffix(" m")
        self.step_spin.setToolTip(
            "Vertical interval used to bin elevations (STEP parameter of the "
            "Hypsometric Curves algorithm)."
        )
        form.addRow("Elevation step:", self.step_spin)

        self.percentage_check = QCheckBox(
            "Use % of area instead of absolute value"
        )
        form.addRow("", self.percentage_check)
        main.addWidget(input_group)

        # --- output / cache
        out_group = QGroupBox("Output")
        out_layout = QVBoxLayout(out_group)

        self.cache_radio = QRadioButton(
            "Managed cache folder (inside the QGIS profile)"
        )
        self.cache_radio.setChecked(True)
        self.custom_radio = QRadioButton("Custom folder:")
        self.dir_widget = QgsFileWidget()
        self.dir_widget.setStorageMode(QgsFileWidget.GetDirectory)
        self.dir_widget.setEnabled(False)
        self.custom_radio.toggled.connect(self.dir_widget.setEnabled)

        custom_row = QHBoxLayout()
        custom_row.addWidget(self.custom_radio)
        custom_row.addWidget(self.dir_widget, 1)
        out_layout.addWidget(self.cache_radio)
        out_layout.addLayout(custom_row)

        self.reuse_check = QCheckBox(
            "Reuse cached results when inputs are unchanged"
        )
        self.reuse_check.setChecked(True)
        out_layout.addWidget(self.reuse_check)

        self.morphometry_check = QCheckBox(
            "Also measure crater depth, diameter and d/D"
        )
        self.morphometry_check.setToolTip(
            "Fits the rim from the DEM for each boundary polygon and appends "
            "depth, diameter and the depth/diameter ratio to the summary CSV, "
            "so the hypsometric curve can be compared against standard "
            "morphometry"
        )
        out_layout.addWidget(self.morphometry_check)
        main.addWidget(out_group)

        # --- run controls
        run_row = QHBoxLayout()
        self.run_button = QPushButton("Run analysis")
        self.run_button.clicked.connect(self._on_run_clicked)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.progress, 1)
        main.addLayout(run_row)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        main.addWidget(self.status_label)

        # --- results tabs
        self.tabs = QTabWidget()

        self.table = QTableWidget(0, len(RESULT_COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in RESULT_COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabs.addTab(self.table, "Results")

        self.plot_label = QLabel("Run the analysis to see the curves plot.")
        self.plot_label.setAlignment(Qt.AlignCenter)
        plot_scroll = QScrollArea()
        plot_scroll.setWidgetResizable(True)
        plot_scroll.setWidget(self.plot_label)
        self.tabs.addTab(plot_scroll, "Curves plot")

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.tabs.addTab(self.log_edit, "Log")
        main.addWidget(self.tabs, 1)

        # --- post-processing hint
        hint_row = QHBoxLayout()
        hint_row.addWidget(QLabel("Post-process with the standalone script:"))
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setReadOnly(True)
        self.cmd_edit.setPlaceholderText(
            "python hypsometric_analysis_v2.py <output>/histogram_*.csv"
        )
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_command)
        hint_row.addWidget(self.cmd_edit, 1)
        hint_row.addWidget(copy_btn)
        main.addLayout(hint_row)

        # --- bottom buttons
        btn_row = QHBoxLayout()
        self.open_folder_btn = QPushButton("Open output folder")
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        self.save_plot_btn = QPushButton("Save plot as…")
        self.save_plot_btn.clicked.connect(self._save_plot_as)
        self.export_csv_btn = QPushButton("Export summary as…")
        self.export_csv_btn.clicked.connect(self._export_summary_as)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        for btn in (self.open_folder_btn, self.save_plot_btn,
                    self.export_csv_btn):
            btn.setEnabled(False)
            btn_row.addWidget(btn)

        # always available: there may be cached runs even with nothing shown
        self.clean_btn = QPushButton("Clean…")
        self.clean_btn.setToolTip(
            "Clear the results, plot and log shown here, and delete the runs "
            "cached in the QGIS profile"
        )
        self.clean_btn.clicked.connect(self._on_clean_clicked)
        btn_row.addWidget(self.clean_btn)

        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        main.addLayout(btn_row)

    # ------------------------------------------------------------- helpers

    def _log(self, message):
        self.log_edit.appendPlainText(str(message))

    def _set_status(self, message):
        self.status_label.setText(message)
        self._log(message)

    def _copy_command(self):
        if self.cmd_edit.text():
            QGuiApplication.clipboard().setText(self.cmd_edit.text())
            self._set_status("Command copied to clipboard.")

    def _open_output_folder(self):
        if self._output_dir and os.path.isdir(self._output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))

    # --------------------------------------------------------- rim detection

    def _boundary_features(self, layer):
        """The features rim detection should seed from."""
        if self.selected_only_check.isChecked():
            return list(layer.getSelectedFeatures())
        return list(layer.getFeatures())

    def _on_detect_rim_clicked(self):
        if self._running:
            QMessageBox.information(
                self, "Hypsometric Analysis Toolkit",
                "A run is in progress. Wait for it to finish first."
            )
            return

        dem = self.dem_combo.currentLayer()
        if dem is None or not dem.isValid():
            QMessageBox.warning(
                self, "Hypsometric Analysis Toolkit",
                "Select a valid DEM raster layer first — the rim is fitted "
                "from its elevations."
            )
            return

        boundary = self.boundary_combo.currentLayer()
        has_polygons = boundary is not None and boundary.isValid()

        options = RimOptionsDialog(
            self, has_polygons=has_polygons,
            dem_size=(dem.width(), dem.height()),
            pixel_size=(dem.rasterUnitsPerPixelX(), dem.rasterUnitsPerPixelY()),
        )
        if options.exec_() != QDialog.Accepted:
            return
        params = options.values()

        if params["mode"] == MODE_POLYGON and not has_polygons:
            QMessageBox.warning(
                self, "Hypsometric Analysis Toolkit",
                "Polygon-seeded detection needs a polygon layer to seed from."
            )
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._detect_rim(dem, boundary, params)
        except Exception as exc:  # noqa: BLE001 - report anything that escapes
            self._set_status(f"Rim detection failed: {exc}")
            QMessageBox.critical(
                self, "Hypsometric Analysis Toolkit",
                f"Rim detection failed:\n{exc}"
            )
        finally:
            QApplication.restoreOverrideCursor()

    def _detect_rim(self, dem, boundary, params):
        source = dem.source().split("|")[0]
        self._set_status(
            f"Reading {os.path.basename(source)} at 1/{params['downsample']} "
            "resolution…"
        )
        QCoreApplication.processEvents()
        z, geo = rim.read_dem(source, params["downsample"])

        detect_args = {
            "n_azimuths": params["n_azimuths"],
            "passes": params["passes"],
            "floor_pct": params["floor_pct"],
        }

        results = []
        failures = []
        if params["mode"] == MODE_WHOLE:
            self._set_status("Fitting the rim over the whole DEM…")
            QCoreApplication.processEvents()
            results.append(("whole DEM",
                            rim.detect_rim(z, geo, mask=None, **detect_args)))
        else:
            features = self._boundary_features(boundary)
            if not features:
                raise ValueError(
                    "the boundary layer has no features to seed from"
                    + (" (selected features only is checked)"
                       if self.selected_only_check.isChecked() else "")
                )
            transform = QgsCoordinateTransform(
                boundary.crs(), dem.crs(), QgsProject.instance())
            for index, feature in enumerate(features, start=1):
                label = f"polygon {feature.id()}"
                self._set_status(
                    f"Fitting the rim inside {label} "
                    f"({index}/{len(features)})…"
                )
                QCoreApplication.processEvents()
                geometry = feature.geometry()
                if geometry is None or geometry.isNull():
                    failures.append((label, "no geometry"))
                    continue
                if boundary.crs() != dem.crs():
                    geometry = QgsGeometry(geometry)
                    geometry.transform(transform)
                try:
                    mask = rim.polygon_mask(geo, geometry.asWkt())
                    results.append(
                        (label, rim.detect_rim(z, geo, mask=mask, **detect_args)))
                except ValueError as exc:
                    failures.append((label, str(exc)))

        for label, reason in failures:
            self._log(f"No rim fitted for {label}: {reason}")

        if not results:
            raise ValueError(
                "no rim could be fitted. Try more rays, a larger polygon, or a "
                "smaller downsample factor; see the Log tab."
            )

        layer = drawing.create_rim_layer(dem.crs())
        if not layer.isValid():
            raise ValueError("could not create the rim layer")
        shape = params.get("shape", SHAPE_CIRCLE)
        shapes = ([SHAPE_CIRCLE, SHAPE_TRACED] if shape == SHAPE_BOTH
                  else [shape])
        for label, result in results:
            for one_shape in shapes:
                if drawing.add_rim_polygon(layer, result, label, one_shape) < 0:
                    self._log(f"{label}: could not build the {one_shape} outline")
            self._log(
                f"{label}: D = {result['diameter_km']:.2f} km, "
                f"centre shift {result['shift_km']:.2f} km, "
                f"rms {result['fit_rms_km']:.3f} km, "
                f"{result['n_found']}/{result['n_rays']} rays, "
                f"{result['quality']}"
            )
            if SHAPE_TRACED in shapes and result.get("traced_area_km2"):
                self._log(
                    f"{label}: traced outline {result['traced_area_km2']:.2f} "
                    f"km2 (circle {result['circle_area_km2']:.2f} km2), "
                    f"{result['n_interpolated']} azimuth(s) interpolated"
                )

        QgsProject.instance().addMapLayer(layer)
        self.boundary_combo.setLayer(layer)

        # keep the fits so a later analysis can report depth without refitting
        by_label = {label: result for label, result in results}
        for feature in layer.getFeatures():
            fit = by_label.get(feature["source"])
            if fit is not None:
                self._rim_fits[(layer.id(), feature.id())] = fit

        flagged = [label for label, result in results
                   if not result["quality"].startswith("ok")]
        summary = (f"Fitted {len(results)} rim circle(s) into "
                   f"'{layer.name()}', now set as the boundary layer.")
        if flagged:
            summary += (f" {len(flagged)} flagged — check centre_shift_km and "
                        "fit_rms_km in the Log tab.")
        self._set_status(summary)

    # --------------------------------------------------------------- drawing

    def _map_canvas(self):
        getter = getattr(self.iface, "mapCanvas", None)
        return getter() if callable(getter) else None

    def _on_draw_toggled(self, checked):
        if checked:
            if not self._start_drawing():
                self.draw_btn.setChecked(False)
        else:
            self._stop_drawing()

    def _ensure_drawn_layer(self):
        """The scratch layer to digitize into, created on first use."""
        project = QgsProject.instance()
        if (self._drawn_layer is not None
                and project.mapLayer(self._drawn_layer.id()) is not None):
            return self._drawn_layer

        crs = project.crs()
        if crs is None or not crs.isValid():
            dem = self.dem_combo.currentLayer()
            crs = dem.crs() if dem is not None else None

        layer = drawing.create_scratch_polygon_layer(crs)
        if not layer.isValid():
            QMessageBox.critical(
                self, "Hypsometric Analysis Toolkit",
                "Could not create the temporary polygon layer."
            )
            return None

        project.addMapLayer(layer)
        self._drawn_layer = layer
        self.boundary_combo.setLayer(layer)
        self._log(
            f"Created temporary layer '{layer.name()}' "
            f"({layer.crs().authid() or 'project CRS'}). It is not saved to "
            "disk; use Layer > Make Permanent to keep it."
        )
        return layer

    def _start_drawing(self):
        canvas = self._map_canvas()
        if canvas is None:
            QMessageBox.warning(
                self, "Hypsometric Analysis Toolkit",
                "The map canvas is not available, so drawing is disabled."
            )
            return False

        try:
            from qgis.gui import QgsMapToolCapture, QgsMapToolDigitizeFeature
        except ImportError as exc:
            QMessageBox.warning(
                self, "Hypsometric Analysis Toolkit",
                f"Digitizing tools are unavailable in this QGIS build:\n{exc}"
            )
            return False

        layer = self._ensure_drawn_layer()
        if layer is None:
            return False

        # the capture modes moved under CaptureMode in newer QGIS versions
        mode = getattr(QgsMapToolCapture, "CapturePolygon", None)
        if mode is None:
            mode = QgsMapToolCapture.CaptureMode.CapturePolygon
        cad_getter = getattr(self.iface, "cadDockWidget", None)
        cad_widget = cad_getter() if callable(cad_getter) else None

        self._draw_tool = QgsMapToolDigitizeFeature(canvas, cad_widget, mode)
        self._draw_tool.setLayer(layer)
        self._draw_tool.digitizingCompleted.connect(self._on_polygon_digitized)
        canvas.setMapTool(self._draw_tool)
        if not self._map_tool_watched:
            canvas.mapToolSet.connect(self._on_map_tool_set)
            self._map_tool_watched = True

        self.draw_btn.setText("Stop drawing")
        self._set_status(
            "Drawing: click vertices on the map canvas, right-click to close "
            f"the polygon. Each one is added to '{layer.name()}'."
        )
        return True

    def _stop_drawing(self):
        tool, self._draw_tool = self._draw_tool, None
        if tool is not None:
            try:
                tool.digitizingCompleted.disconnect(self._on_polygon_digitized)
            except (TypeError, RuntimeError):
                pass
            canvas = self._map_canvas()
            if canvas is not None and canvas.mapTool() is tool:
                canvas.unsetMapTool(tool)
        self.draw_btn.setText("Draw polygon")
        if self.draw_btn.isChecked():
            self.draw_btn.setChecked(False)

    def _on_map_tool_set(self, new_tool, _old_tool=None):
        """Untoggle the button when QGIS switches to a different map tool."""
        if self._draw_tool is not None and new_tool is not self._draw_tool:
            self._stop_drawing()

    def _on_polygon_digitized(self, feature):
        layer = self._drawn_layer
        if layer is None:
            return
        if QgsProject.instance().mapLayer(layer.id()) is None:
            self._drawn_layer = None
            self._stop_drawing()
            self._set_status(
                "The temporary polygon layer was removed, so drawing stopped."
            )
            return

        count = drawing.add_polygon(layer, feature.geometry())
        if count < 0:
            self._set_status("The digitized polygon could not be added.")
            return

        self.boundary_combo.setLayer(layer)
        self._set_status(
            f"Polygon {count} added to '{layer.name()}'. Draw another, or stop "
            "drawing and run the analysis."
        )

    def closeEvent(self, event):
        self._stop_drawing()
        super().closeEvent(event)

    # ----------------------------------------------------------------- clean

    def _reset_view(self):
        """Clear everything a run produced, leaving the input choices alone."""
        self._results = []
        self._output_dir = None
        self._plot_path = None
        self.table.setRowCount(0)
        self.plot_label.clear()
        self.plot_label.setText("Run the analysis to see the curves plot.")
        self.cmd_edit.clear()
        self.log_edit.clear()
        self.progress.setValue(0)
        for btn in (self.open_folder_btn, self.save_plot_btn,
                    self.export_csv_btn):
            btn.setEnabled(False)

    def _on_clean_clicked(self):
        if self._running:
            QMessageBox.information(
                self, "Hypsometric Analysis Toolkit",
                "A run is in progress. Cancel it before cleaning."
            )
            return

        cache_root = qgis_runner.default_cache_root()
        runs, size = qgis_runner.cache_summary(cache_root)

        # Nothing to delete: clearing the view alone needs no confirmation.
        if runs == 0:
            self._reset_view()
            self._set_status(
                "Cleared the results, plot and log. No cached runs to delete."
            )
            return

        answer = QMessageBox.question(
            self, "Clean up",
            f"Delete {runs} cached run(s) ({qgis_runner.format_size(size)}) from:\n"
            f"{cache_root}\n\n"
            "The results, plot and log shown here will also be cleared.\n"
            "Anything written to a custom output folder is left untouched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        removed, freed = qgis_runner.clear_cache(cache_root)
        self._reset_view()
        self._set_status(
            f"Cleaned: deleted {removed} cached run(s), "
            f"freed {qgis_runner.format_size(freed)}."
        )

    def _save_plot_as(self):
        if not self._plot_path or not os.path.isfile(self._plot_path):
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Save plot", "hypsometric_curves.png", "PNG image (*.png)"
        )
        if target:
            import shutil
            shutil.copyfile(self._plot_path, target)
            self._set_status(f"Plot saved: {target}")

    def _export_summary_as(self):
        if not self._results:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Export summary CSV", qgis_runner.SUMMARY_NAME,
            "CSV file (*.csv)"
        )
        if target:
            analysis.write_summary_csv(self._results, target)
            self._set_status(f"Summary exported: {target}")

    # ----------------------------------------------------------------- run

    def _on_run_clicked(self):
        if self._running:
            if self._feedback is not None:
                self._feedback.cancel()
                self._set_status("Cancelling…")
            return
        self._run()

    def _validate(self):
        dem = self.dem_combo.currentLayer()
        boundary = self.boundary_combo.currentLayer()
        if dem is None or not dem.isValid():
            return None, None, "Select a valid DEM raster layer."
        if boundary is None or not boundary.isValid():
            return None, None, "Select a valid polygon boundary layer."
        if (self.selected_only_check.isChecked()
                and boundary.selectedFeatureCount() == 0):
            return None, None, (
                "'Selected features only' is checked but the boundary layer "
                "has no selected features."
            )
        return dem, boundary, None

    def _resolve_output_dir(self, params_hash):
        if self.custom_radio.isChecked():
            path = self.dir_widget.filePath().strip()
            if not path:
                return None, "Choose a custom output folder (or use the cache)."
            return path, None
        return os.path.join(qgis_runner.default_cache_root(), params_hash), None

    def _run(self):
        dem, boundary, error = self._validate()
        if error:
            QMessageBox.warning(self, "Hypsometric Analysis Toolkit", error)
            return

        selected_only = self.selected_only_check.isChecked()
        step = self.step_spin.value()
        use_pct = self.percentage_check.isChecked()
        self._last_dem, self._last_boundary = dem, boundary

        params_hash = qgis_runner.compute_params_hash(
            dem, boundary, selected_only, step, use_pct
        )
        output_dir, error = self._resolve_output_dir(params_hash)
        if error:
            QMessageBox.warning(self, "Hypsometric Analysis Toolkit", error)
            return

        # Cached run?
        if self.reuse_check.isChecked():
            cached = qgis_runner.find_cached_run(output_dir, params_hash)
            if cached:
                self._set_status(
                    f"Reusing {len(cached)} cached CSV(s) from {output_dir}"
                )
                self._finish(output_dir, cached, from_cache=True)
                return

        os.makedirs(output_dir, exist_ok=True)
        stale = qgis_runner.list_output_csvs(output_dir)
        if stale:
            if self.custom_radio.isChecked():
                self._log(
                    f"Warning: {len(stale)} existing histogram_*.csv file(s) "
                    "in the output folder will be overwritten or may mix with "
                    "this run."
                )
            else:
                for path in stale:  # managed cache: always start clean
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        alg = qgis_runner.get_algorithm()
        if alg is None:
            QMessageBox.critical(
                self, "Hypsometric Analysis Toolkit",
                "Algorithm 'qgis:hypsometriccurves' was not found. Make sure "
                "the Processing plugin is enabled."
            )
            return

        params = qgis_runner.build_params(
            dem, boundary, selected_only, step, use_pct, output_dir
        )

        self._feedback = _DialogFeedback(self._log)
        self._feedback.progressChanged.connect(self._on_progress)
        self._running = True
        self.run_button.setText("Cancel")
        self.progress.setValue(0)
        self._set_status("Running qgis:hypsometriccurves…")

        try:
            qgis_runner.run_algorithm(params, feedback=self._feedback)
            cancelled = self._feedback.isCanceled()
        except Exception as exc:  # noqa: BLE001 - surface any processing error
            self._running = False
            self.run_button.setText("Run analysis")
            if self._feedback is not None and self._feedback.isCanceled():
                self._set_status("Run cancelled.")
                return
            self._set_status(f"Processing failed: {exc}")
            QMessageBox.critical(
                self, "Hypsometric Analysis Toolkit",
                f"Processing failed:\n{exc}"
            )
            return

        self._running = False
        self.run_button.setText("Run analysis")

        if cancelled:
            self._set_status("Run cancelled.")
            return

        csvs = qgis_runner.list_output_csvs(output_dir)
        if not csvs:
            self._set_status(
                "The algorithm finished but produced no CSV files. Check that "
                "the polygons intersect the DEM (see the Log tab)."
            )
            return

        qgis_runner.write_manifest(
            output_dir, params_hash,
            extra={
                "dem": dem.source(),
                "boundary": boundary.source(),
                "step": step,
                "use_percentage": use_pct,
                "selected_only": selected_only,
            },
        )
        self._finish(output_dir, csvs, from_cache=False)

    def _on_progress(self, value):
        self.progress.setValue(int(value))
        QCoreApplication.processEvents()

    # -------------------------------------------------------------- results

    def _finish(self, output_dir, csv_paths, from_cache):
        self._output_dir = output_dir
        self._results, errors = analysis.analyze_files(csv_paths)
        for path, message in errors:
            self._log(f"Skipped {path}: {message}")

        if self.morphometry_check.isChecked():
            self._add_morphometry()

        self._populate_table()

        summary_path = os.path.join(output_dir, qgis_runner.SUMMARY_NAME)
        analysis.write_summary_csv(self._results, summary_path)

        self._plot_path = None
        if self._results:
            try:
                self._plot_path = plotting.plot_curves(
                    self._results,
                    os.path.join(output_dir, qgis_runner.PLOT_NAME),
                )
            except Exception as exc:  # noqa: BLE001
                self._log(f"Plotting failed: {exc}")
        self._update_plot_preview()

        self.cmd_edit.setText(
            "python hypsometric_analysis_v2.py "
            f'"{os.path.join(output_dir, "histogram_")}"*.csv'
        )

        self.open_folder_btn.setEnabled(True)
        self.export_csv_btn.setEnabled(bool(self._results))
        self.save_plot_btn.setEnabled(bool(self._plot_path))
        self.progress.setValue(100)

        source = "cache" if from_cache else "processing run"
        self._set_status(
            f"Analyzed {len(self._results)} feature(s) from {source}. "
            f"Outputs in: {output_dir}"
        )

    def _add_morphometry(self):
        """
        Append depth, diameter and d/D to each analysed feature.

        Uses the rim already fitted by Detect rim when there is one, and
        otherwise fits one inside the boundary polygon, so this works for drawn
        and prepared polygons too.
        """
        # fall back to the current selection, so this does not depend on
        # state left behind by the run that produced the results
        dem = self._last_dem or self.dem_combo.currentLayer()
        boundary = self._last_boundary or self.boundary_combo.currentLayer()
        if dem is None or boundary is None:
            self._log("Depth not measured: the run's layers are unavailable.")
            return

        try:
            source = dem.source().split("|")[0]
            downsample = rim.suggested_downsample(dem.width(), dem.height())
            self._set_status(f"Measuring depth and d/D from {os.path.basename(source)}…")
            QCoreApplication.processEvents()
            z, geo = rim.read_dem(source, downsample)
        except Exception as exc:  # noqa: BLE001
            self._log(f"Depth not measured: could not read the DEM ({exc}).")
            return

        features = {feature.id(): feature
                    for feature in self._boundary_features(boundary)}
        transform = QgsCoordinateTransform(
            boundary.crs(), dem.crs(), QgsProject.instance())

        measured = 0
        for result in self._results:
            fid = analysis.feature_fid_from_path(result["file_path"])
            label = result["feature_id"]
            if fid is None or fid not in features:
                self._log(f"{label}: no matching polygon, depth not measured.")
                continue
            try:
                fit = self._rim_fits.get((boundary.id(), fid))
                if fit is None:
                    geometry = features[fid].geometry()
                    if geometry is None or geometry.isNull():
                        raise ValueError("the polygon has no geometry")
                    if boundary.crs() != dem.crs():
                        geometry = QgsGeometry(geometry)
                        geometry.transform(transform)
                    mask = rim.polygon_mask(geo, geometry.asWkt())
                    fit = rim.detect_rim(z, geo, mask=mask)
                    self._rim_fits[(boundary.id(), fid)] = fit
                result.update(morphometry.compute(z, geo, fit))
                measured += 1
                self._log(
                    f"{label}: D = {result['diameter_km']:.2f} km, "
                    f"depth = {result['depth_rim_to_floor_m']:.0f} m "
                    f"(alt floor {result['depth_alt_floor_m']:.0f} m), "
                    f"d/D = {result['d_over_D']:.4f}, "
                    f"{result['rim_confidence']}"
                )
            except Exception as exc:  # noqa: BLE001
                self._log(f"{label}: depth not measured ({exc}).")

        if measured:
            self._log(
                f"Measured depth and d/D for {measured} of "
                f"{len(self._results)} feature(s)."
            )

    def _populate_table(self):
        self.table.setRowCount(len(self._results))
        for row, res in enumerate(self._results):
            for col, (key, _label) in enumerate(RESULT_COLUMNS):
                value = res.get(key)
                if isinstance(value, float):
                    if "integral" in key:
                        text = f"{value:.3f}"
                    else:
                        text = f"{value:,.1f}"
                elif value is None:
                    text = "—"
                else:
                    text = str(value)
                item = QTableWidgetItem(text)
                if isinstance(value, float):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def _update_plot_preview(self):
        if not self._plot_path or not os.path.isfile(self._plot_path):
            self.plot_label.setText("No plot available.")
            return
        pixmap = QPixmap(self._plot_path)
        if pixmap.isNull():
            self.plot_label.setText("Could not load plot image.")
            return
        self.plot_label.setPixmap(
            pixmap.scaledToWidth(
                max(600, self.tabs.width() - 60), Qt.SmoothTransformation
            )
        )
