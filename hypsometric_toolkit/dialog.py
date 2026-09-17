"""
Main dialog of the Hypsometric Analysis Toolkit plugin.

UI is built in code (no .ui file) so the plugin runs straight from a zip.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication, QPixmap
from qgis.PyQt.QtWidgets import (
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
from qgis.core import QgsMapLayerProxyModel, QgsProcessingFeedback
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox

from .core import analysis, plotting, qgis_runner

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
        form.addRow("Boundary polygons:", self.boundary_combo)

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
