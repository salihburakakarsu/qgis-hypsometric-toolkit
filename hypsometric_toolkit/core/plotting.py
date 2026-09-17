"""
Hypsometric curve plotting, matching the combined relative-mode figure of the
latest hypsometric_analysis_v2.py (github.com/salihburakakarsu/hypsometric-analysis):
Strahler convention with relative area (a/A) on X and relative elevation (h/H)
on Y, explanatory footer box, legend outside the plot area.

Two renderers produce the same figure:
- matplotlib (used when the QGIS Python bundles it, e.g. QGIS-LTR 3.34 macOS);
- a pure-Qt QPainter fallback with no dependencies beyond QGIS itself
  (e.g. QGIS 3.44 macOS builds ship without matplotlib).

Callers just use plot_curves(); the renderer is picked automatically.
"""

try:
    import matplotlib
    matplotlib.use("Agg")  # never require a Qt canvas
    import matplotlib.pyplot as plt
    import numpy as np
    HAVE_MATPLOTLIB = True
except ImportError:  # pragma: no cover
    HAVE_MATPLOTLIB = False

# matplotlib tab10 palette
TAB10 = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

XLABEL = "Relative Area (a/A)"
XLABEL_SUB = "[a = cumulative area, A = total area]"
YLABEL = "Relative Elevation (h/H)"
YLABEL_SUB = "[h = height above minimum, H = total height range]"
EXPLANATION = (
    "Relative Area (a/A): Normalized cumulative area from total (A) to zero\n"
    "Relative Elevation (h/H): Normalized elevation from minimum (0) to maximum (1)\n"
    "where: a = cumulative area, A = total area, h = elevation above minimum, "
    "H = total elevation range\n"
    "Hypsometric Integral (HI) = area under the curve (shows landform maturity)"
)


def _plottable(results):
    return [r for r in results if r.get("rel_elevation") is not None]


def plot_curves(results, png_path, title=None):
    """
    Plot the hypsometric curves of analyze_csv() results that have curve data
    and save as PNG. Returns png_path, or None if nothing plottable.
    """
    if HAVE_MATPLOTLIB:
        return _plot_curves_mpl(results, png_path, title)
    return _plot_curves_qt(results, png_path, title)


# --------------------------------------------------------------- matplotlib

def _plot_curves_mpl(results, png_path, title=None):
    plottable = _plottable(results)
    if not plottable:
        return None

    plt.figure(figsize=(12, 8))
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(plottable), 1)))

    for i, res in enumerate(plottable):
        hi = res["hypsometric_integral_curve"]
        color = colors[i % len(colors)]
        plt.plot(res["rel_area"], res["rel_elevation"],
                 label=f"{res['feature_id']} (HI={hi:.3f})",
                 color=color, linewidth=2)
        plt.fill_between(res["rel_area"], 0, res["rel_elevation"],
                         alpha=0.3, color=color)

    plt.xlabel(f"{XLABEL}\n{XLABEL_SUB}", fontsize=12)
    plt.ylabel(f"{YLABEL}\n{YLABEL_SUB}", fontsize=12)
    plt.title(title
              or f"Hypsometric Curves Analysis ({len(plottable)} features)",
              fontsize=14, fontweight="bold")
    plt.grid(True, alpha=0.3)
    plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    plt.xlim(0, 1)
    plt.ylim(0, 1)

    plt.figtext(0.5, -0.02, EXPLANATION, fontsize=9, style="italic",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightgray",
                          alpha=0.8),
                horizontalalignment="center", verticalalignment="top")

    plt.tight_layout()
    plt.savefig(str(png_path), dpi=200, bbox_inches="tight")
    plt.close()
    return str(png_path)


# ------------------------------------------------------------- Qt fallback

def _plot_curves_qt(results, png_path, title=None, width=1500, height=1050):
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt
    from qgis.PyQt.QtGui import (
        QColor,
        QFont,
        QFontMetrics,
        QImage,
        QPainter,
        QPen,
        QPolygonF,
    )

    plottable = _plottable(results)
    if not plottable:
        return None

    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("white"))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        tick_font = QFont()
        tick_font.setPointSize(14)
        label_font = QFont()
        label_font.setPointSize(16)
        sub_font = QFont()
        sub_font.setPointSize(12)
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        note_font = QFont()
        note_font.setPointSize(11)
        note_font.setItalic(True)

        text_color = QColor(60, 60, 60)

        legend_entries = []
        for i, res in enumerate(plottable):
            hi = res["hypsometric_integral_curve"]
            legend_entries.append((QColor(TAB10[i % len(TAB10)]),
                                   f"{res['feature_id']} (HI={hi:.3f})"))

        # legend sits outside the plot, so reserve right margin for it
        metrics = QFontMetrics(tick_font)
        sample_w, pad = 34, 12
        row_h = metrics.height() + 8
        legend_text_w = max(metrics.horizontalAdvance(t)
                            for _c, t in legend_entries)
        legend_w = pad + sample_w + 8 + legend_text_w + pad

        margin_l, margin_t = 150, 75
        margin_r = legend_w + 40
        margin_b = 245  # tick labels + 2-line x label + explanation box
        plot = QRectF(margin_l, margin_t,
                      width - margin_l - margin_r,
                      height - margin_t - margin_b)

        def px(x):
            return plot.left() + x * plot.width()

        def py(y):
            return plot.bottom() - y * plot.height()

        # grid + tick labels
        p.setFont(tick_font)
        for i in range(6):
            v = i / 5.0
            p.setPen(QPen(QColor(0, 0, 0, 28), 1))
            p.drawLine(QPointF(px(v), plot.top()),
                       QPointF(px(v), plot.bottom()))
            p.drawLine(QPointF(plot.left(), py(v)),
                       QPointF(plot.right(), py(v)))
            p.setPen(QPen(text_color))
            p.drawText(QRectF(px(v) - 40, plot.bottom() + 10, 80, 26),
                       Qt.AlignHCenter | Qt.AlignTop, f"{v:.1f}")
            p.drawText(QRectF(plot.left() - 78, py(v) - 13, 66, 26),
                       Qt.AlignRight | Qt.AlignVCenter, f"{v:.1f}")

        # curves: x = relative area (a/A), y = relative elevation (h/H)
        p.setClipRect(plot)
        for i, res in enumerate(plottable):
            color = QColor(TAB10[i % len(TAB10)])
            pts = [QPointF(px(float(x)), py(float(y)))
                   for x, y in zip(res["rel_area"], res["rel_elevation"])]
            fill = QPolygonF(pts)
            fill.append(QPointF(pts[-1].x(), py(0.0)))
            fill.append(QPointF(pts[0].x(), py(0.0)))
            fill_color = QColor(color)
            fill_color.setAlpha(70)
            p.setPen(Qt.NoPen)
            p.setBrush(fill_color)
            p.drawPolygon(fill)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 3.0))
            p.drawPolyline(QPolygonF(pts))
        p.setClipping(False)

        # x-axis label + sub-label
        p.setPen(QPen(text_color))
        p.setFont(label_font)
        p.drawText(QRectF(plot.left(), plot.bottom() + 44, plot.width(), 30),
                   Qt.AlignHCenter | Qt.AlignVCenter, XLABEL)
        p.setFont(sub_font)
        p.drawText(QRectF(plot.left(), plot.bottom() + 76, plot.width(), 26),
                   Qt.AlignHCenter | Qt.AlignVCenter, XLABEL_SUB)

        # y-axis label + sub-label (rotated)
        p.save()
        p.translate(38, plot.center().y())
        p.rotate(-90)
        p.setFont(label_font)
        p.drawText(QRectF(-plot.height() / 2, -15, plot.height(), 30),
                   Qt.AlignHCenter | Qt.AlignVCenter, YLABEL)
        p.setFont(sub_font)
        p.drawText(QRectF(-plot.height() / 2, 17, plot.height(), 26),
                   Qt.AlignHCenter | Qt.AlignVCenter, YLABEL_SUB)
        p.restore()

        # title
        p.setFont(title_font)
        p.setPen(QPen(QColor(20, 20, 20)))
        p.drawText(QRectF(0, 12, width, 44), Qt.AlignHCenter | Qt.AlignTop,
                   title
                   or f"Hypsometric Curves Analysis ({len(plottable)} features)")

        # legend (outside, top-right)
        p.setFont(tick_font)
        box_h = pad + row_h * len(legend_entries) + pad - 8
        box = QRectF(plot.right() + 18, plot.top(), legend_w, box_h)
        p.setPen(QPen(QColor(150, 150, 150)))
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(box, 4, 4)
        for i, (color, text) in enumerate(legend_entries):
            y = box.top() + pad + i * row_h + row_h / 2 - 4
            p.setPen(QPen(color, 3.0))
            p.drawLine(QPointF(box.left() + pad, y),
                       QPointF(box.left() + pad + sample_w, y))
            p.setPen(QPen(text_color))
            p.drawText(QRectF(box.left() + pad + sample_w + 8,
                              y - row_h / 2, legend_text_w + 4, row_h),
                       Qt.AlignLeft | Qt.AlignVCenter, text)

        # explanation footer box
        p.setFont(note_font)
        note_metrics = QFontMetrics(note_font)
        lines = EXPLANATION.split("\n")
        note_w = max(note_metrics.horizontalAdvance(ln) for ln in lines) + 40
        note_h = note_metrics.height() * len(lines) + 24
        note = QRectF((width - note_w) / 2, height - note_h - 16,
                      note_w, note_h)
        p.setPen(QPen(QColor(150, 150, 150)))
        p.setBrush(QColor(211, 211, 211, 205))
        p.drawRoundedRect(note, 6, 6)
        p.setPen(QPen(text_color))
        p.drawText(note, Qt.AlignCenter, EXPLANATION)
    finally:
        p.end()

    if not img.save(str(png_path)):
        raise RuntimeError(f"could not write {png_path}")
    return str(png_path)
