"""Read-only self-diagnostics: one report of WHERE the render chain breaks.

Why this lives in the plugin rather than in a console snippet: a user reported
"nothing happens on the canvas", and from the outside that single symptom has
at least six distinct causes — the extension was never registered, no model
exists for the active document, no active view, the canvas widget was not
found, the plausibility gate vetoed it, or the document size could not be
read. Each one needs a different fix, and guessing which one it is produces
exactly the kind of blind patching this project forbids. So the plugin can
describe its own state on demand.

Contract:

* READ-ONLY. Nothing here creates, binds, rebinds or mutates a context, a
  model, a view or a document: it only calls getters. ``Document.width()``,
  ``Canvas.documentRectInWidgetPixels()`` etc. are reads and are the same
  reads the plugin already performs (the invariant "the grid never
  touches a Document mutation API" holds here too — see the repo-wide rule).
  That also covers the computed-path inputs — ``Canvas.
  zoomLevel()/preferredCenter()/rotation()/mirror()`` and
  ``Document.width()/height()`` — which the report prints as data lines. The
  scale rule is ``zoomLevel()`` as such (the live 5.3.4.1 host proved it: the
  two scrollbar values -212/-284 agree with the computed rect only for
  ``s = zoom``; see :func:`composition_grid.canvas.view_metrics.
  scale_per_doc_px`), so ``Document.resolution()/xRes()/yRes()`` are printed as
  INFORMATION ONLY and the report cross-checks its own numbers against
  ``QScrollBar.value()`` and prints the deltas — the line that would expose a
  host in the other (physical-size) zoom semantics. It adds the
  owning scroll area's ``QAbstractSlider`` readings: the METHODS
  ``minimum()/maximum()/value()/pageStep()`` (with a fallback to the Qt
  properties of the same names, because PyQt5 exposes the methods and a
  ``minimumValue()`` call there is an error string, not a number) plus the
  context's bind-time trigger snapshot — all read-only, all printed so ONE live
  report settles the placement convention, the scale rule, the wiring state and
  the rebind visibility without a Python console.
* NO ``krita`` import and NO Qt binding: the libkis ``app`` object arrives as
  a parameter (the caller — a Krita action — resolves ``Krita.instance()``),
  and Qt comes through :mod:`composition_grid.qt_compat`. That keeps this
  module importable headlessly and testable without a running Krita.
* EVERY host call is defensive. libkis objects can be mid-teardown and raise
  from any getter, and a diagnostic that dies halfway hides the line the user
  needs; so each probe catches, records the failure as a line and continues.

The report is a flat list of ``OK  ``/``FAIL`` lines in chain order: the FIRST
``FAIL`` names the broken link.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger("composition_grid")

__all__ = ["REPORT_PATH", "collect_report", "write_report"]

REPORT_PATH = Path(tempfile.gettempdir()) / "composition_grid_diagnostics.txt"
"""Where :func:`write_report` drops the text, so the user can attach a file
instead of transcribing a dialog."""

_OK = "OK  "
_FAIL = "FAIL"

_ABSENT = object()
"""Sentinel for "the object does not expose this member at all" — distinct
from ``None``, which is a real (unbound) value on the production context."""


class _Capture(logging.Handler):
    """Collects the plugin's own log records into the report.

    The plugin writes every degradation (absent signal, locator fallback,
    plausibility veto, missing document size) to the ``composition_grid``
    logger, but installs no handler of its own — outside a terminal-launched
    Krita those messages go nowhere. Capturing them here turns "we hid the
    grid on purpose" into visible evidence."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(f"{record.levelname}: {record.getMessage()}")
        except Exception:  # noqa: BLE001 — a diagnostic never raises outward
            pass


def _mark(condition: bool) -> str:
    return _OK if condition else _FAIL


def _describe(value) -> str:
    """Short, always-safe rendering of a host object (libkis wrappers may
    raise from ``repr`` during teardown)."""
    if value is None:
        return "None"
    if isinstance(value, str):
        return value
    try:
        return f"{type(value).__name__}@0x{id(value):x}"
    except Exception as exc:  # noqa: BLE001
        return f"<unprintable: {exc!r}>"


def _probe(target, method_name: str) -> Tuple[object, bool]:
    """Call ``target.method_name()`` defensively.

    Answers ``(value_or_error_text, succeeded)``: a getter that is absent or
    raises becomes a report line, never an exception out of the probe."""
    if target is None:
        return f"<нет объекта для {method_name}>", False
    getter = getattr(target, method_name, None)
    if not callable(getter):
        return f"<у {type(target).__name__} нет метода {method_name}>", False
    try:
        return getter(), True
    except Exception as exc:  # noqa: BLE001
        return f"{method_name}() бросил {exc!r}", False


def collect_report(app, extension=None) -> str:
    """Build the chain report for the running ``app`` (a libkis ``Krita``
    instance) and the plugin ``extension`` (``None`` -> the module's own
    registration state is still reported)."""
    lines: List[str] = []
    capture = _Capture()
    plugin_logger = logging.getLogger("composition_grid")
    previous_level = plugin_logger.level
    plugin_logger.addHandler(capture)
    plugin_logger.setLevel(logging.DEBUG)
    try:
        _build(lines, app, extension)
    except Exception as exc:  # noqa: BLE001 — the report must always come back
        logger.exception("diagnostics: report building failed")
        lines.append(f"{_FAIL} диагностика оборвалась: {exc!r}")
    finally:
        plugin_logger.removeHandler(capture)
        plugin_logger.setLevel(previous_level)

    lines.append("")
    lines.append("--- лог composition_grid за время прогона ---")
    lines.extend(
        f"  {entry}"
        for entry in capture.records
        or ["(сообщений нет: плагин ничего не логировал)"]
    )
    return "\n".join(lines)


def _host_surface(obj) -> str:
    """Public callables of a libkis object, sorted — the host API surface.

    WHY: the live report showed Canvas WITHOUT any of the signals the design
    was built on. Whether that is a version we do not support, a differently
    wrapped API, or an object we are probing wrongly cannot be decided from a
    yes/no line; the method list answers it in one shot, and the fix must be
    chosen against the API the host actually has rather than against the one a
    report document promises."""
    if obj is None:
        return "None"
    try:
        names = sorted(
            name
            for name in dir(obj)
            if not name.startswith("_") and callable(getattr(obj, name, None))
        )
    except Exception as exc:  # noqa: BLE001
        return f"<поверхность недоступна: {exc!r}>"
    return ", ".join(names) if names else "<пусто>"


def _krita_version_probe(app) -> "tuple[str | None, list]":
    """Read the documented libkis version API: the instance ``app.version()``."""
    try:
        value = app.version()
    except Exception as exc:  # noqa: BLE001
        return None, [("app.version()", f"<{exc!r}>")]
    if isinstance(value, str) and value.strip():
        return value.strip(), [("app.version()", value.strip())]
    return None, [("app.version()", f"<нет версии: {value!r}>")]


def _slider_number(bar, name: str) -> Tuple[object, bool]:
    """One ``QAbstractSlider`` number, read the way the binding exposes it.

    A real-host quirk: the live report once printed ``min '<у QScrollBar нет метода
    minimumValue>'`` — ``minimumValue``/``maximumValue`` are the Qt *property*
    names, while the *methods* are ``minimum()``/``maximum()``/``value()``/
    ``pageStep()`` — and PyQt5 (the user's binding) exposes only the methods.
    This probes the member generically: callable → call it (the method, both
    bindings), otherwise take the attribute (a binding that exposes the property
    and no method). Either way the report gets a NUMBER; a genuine absence still
    answers as an honest error string, because a diagnostic that invents 0 would
    be worse than one that says it could not read."""
    if bar is None:
        return "<нет объекта для {0}>".format(name), False
    member = getattr(bar, name, None)
    if callable(member):
        try:
            return member(), True
        except Exception as exc:  # noqa: BLE001 — a widget may be mid-teardown
            return f"{name}() бросил {exc!r}", False
    if member is not None:
        return member, True
    return (
        f"<у {type(bar).__name__} нет {name}: ни метода, ни свойства>",
        False,
    )


def _attached_widget(extension):
    """The canvas widget the plugin has actually attached (the viewport whose
    size the computed ViewState is measured against), or ``None`` when no
    context has one. A read of the holder's own state: nothing is bound here."""
    try:
        contexts = tuple(getattr(extension, "contexts", ()) or ())
    except Exception as exc:  # noqa: BLE001 — a host getter never breaks a report
        logger.debug("diagnostics: the context list is unavailable: %r", exc)
        return None
    for context in contexts:
        widget = getattr(context, "widget", None)
        if widget is not None:
            return widget
    return None


def _scrollbar_pairs(extension) -> List[Tuple[str, object]]:
    """``(label, bar)`` for the scrollbars the bound context actually wired at
    bind time (the same snapshot :func:`_trigger_wiring_lines` prints).

    WHY the report needs them in the METRICS block: the host's own bar values are
    the only independent measurement of where the document really sits in widget
    px, and the scale rule is only falsifiable when the computed rect and those
    values are printed side by side (:func:`_cross_check_lines`). Pure introspec-
    tion of the context's stored state — nothing is bound, looked up or mutated."""
    try:
        contexts = tuple(getattr(extension, "contexts", ()) or ())
    except Exception as exc:  # noqa: BLE001 — a host getter never breaks a report
        logger.debug("diagnostics: the context list is unavailable: %r", exc)
        return []
    for context in contexts:
        status = getattr(context, "trigger_status", None)
        if not isinstance(status, dict):
            continue
        bars = status.get("bars") or []
        if bars:
            return list(zip(("horizontal", "vertical"), bars))
    return []


def _xy(point, name: str):
    """``point.x()`` / ``point.y()`` defensively — a ``QPointF`` duck from the
    host, possibly mid-teardown."""
    value, ok = _probe(point, name)
    return value if ok else None


def _number(value):
    """A host number as ``float``, or ``None`` when it is not a number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _host_affine_lines(lines: List[str], affine) -> None:
    """Print the host's own doc->widget matrix, or why it cannot be had.

    WHY this stands next to the computed rect: the computed placement
    reconstructs the view from ``zoomLevel``/``preferredCenter``/``rotation``,
    and that reconstruction is exact only for an UNROTATED view (Krita 5.3
    rotation is clockwise-positive and pivots away from the doc centre — see
    :mod:`composition_grid.canvas.doc_view_mapper`). ``View.
    flakeToImageTransform()``/``flakeToCanvasTransform()`` answer the real
    matrix, are what :attr:`CanvasContext.view_state` prefers, and settle the
    rotation question in one report. The numbers are the ``Affine`` entries
    (a, b, c, d, e, f) mapping ``(x, y) -> (a*x + c*y + e, b*x + d*y + f)``,
    plus where they put the document's top-left corner — a point an operator
    can measure on the screen."""
    emit = lines.append
    if affine is None:
        emit(
            f"{_FAIL} аффинное преобразование хоста doc->widget "
            "(View.flakeToImageTransform + flakeToCanvasTransform): недоступно — "
            "нет геттеров / сингулярная матрица; работает вычисленный rect "
            "(точный только при повороте 0)"
        )
        return
    origin_px = affine.apply((0.0, 0.0))
    scale = abs(affine.a * affine.d - affine.b * affine.c) ** 0.5
    emit(
        f"{_OK} аффинное преобразование хоста doc->widget = "
        f"a {affine.a!r}, b {affine.b!r}, c {affine.c!r}, d {affine.d!r}, "
        f"e {affine.e!r}, f {affine.f!r} — doc(0, 0) -> widget "
        f"({origin_px[0]:.1f}, {origin_px[1]:.1f}), "
        f"масштаб sqrt(|det|) = {scale!r}"
    )


def _computed_metrics_lines(
    lines: List[str], canvas, document, widget, bars=(), host_affine=None
) -> None:
    """Print every input the COMPUTED ViewState is built from, the result, and
    the independent host measurement that can refute it.

    WHY this is in the report: on Krita
    5.3.x the ViewState has no signal source at all (see the module-level note),
    so it is reconstructed from getters as
    ``scale = Canvas.zoomLevel()`` with ``preferredCenter`` placed at the
    viewport centre — NO ``resolution/72`` normalisation, because the live
    5.3.4.1 host's own scrollbar values (-212 horizontal, -284 vertical,
    ``zoomLevel() = 0.0833333333333357``, ``preferredCenter = (4860, 1854)``,
    viewport 1234x877) agree with that rule on BOTH axes and with the
    DPI-normalised variant on neither (see
    :func:`composition_grid.canvas.view_metrics.scale_per_doc_px`).
    ``Document.resolution()/xRes()/yRes()`` are therefore printed as pure
    reference data, and the block closes with :func:`_cross_check_lines`, which
    recomputes the agreement inside the report itself. A headless test can pin
    the arithmetic but cannot decide whether it matches a real screen — so one
    report either confirms or refutes it. All reads, ``_probe``-guarded, in the
    flat OK/FAIL data-line style of the rest of the report.

    Between the computed rect and the cross-check sits the host's OWN
    doc->widget affine (:func:`_host_affine_lines`): under a rotated view that
    matrix, not the computed rect above it, is what ``CanvasContext.view_state``
    actually hands the overlay.
    """
    from composition_grid.canvas import host_view_probe
    from composition_grid.canvas.doc_view_mapper import view_scale
    from composition_grid.canvas.view_metrics import to_view_state

    zoom, zoom_ok = _probe(canvas, "zoomLevel")
    emit = lines.append
    emit(f"{_mark(zoom_ok)} Canvas.zoomLevel() = {zoom!r}")

    center, center_ok = _probe(canvas, "preferredCenter")
    center_x = _xy(center, "x") if center_ok and center is not None else None
    center_y = _xy(center, "y") if center_ok and center is not None else None
    emit(
        f"{_mark(center_x is not None and center_y is not None)} "
        f"Canvas.preferredCenter() = x {center_x!r}, y {center_y!r}"
    )

    # INFORMATIONAL ONLY: the corrected scale never divides by the dpi. Kept in
    # the report because it is the number the refuted rule used, so a host that
    # DOES run in physical-size mode is visible here (and in the cross-check).
    resolution, resolution_ok = _probe(document, "resolution")
    emit(
        f"{_mark(resolution_ok)} Document.resolution() = {resolution!r} — справочно: "
        "в вычислении масштаба не участвует (5.3.4.1: масштаб = zoomLevel() как есть)"
    )

    # xRes/yRes used to matter
    # because a single dpi was folded into the scale; with the direct-zoom rule
    # anisotropy cannot hurt it. Printed anyway as reference data — a host whose
    # dpi differs per axis shows up in the scrollbar cross-check instead.
    x_res, xres_ok = _probe(document, "xRes")
    y_res, yres_ok = _probe(document, "yRes")
    emit(
        f"{_mark(xres_ok and yres_ok)} Document.xRes() = {x_res!r}, "
        f"Document.yRes() = {y_res!r} — справочно: при xRes != yRes масштаб "
        "здесь всё равно одинаков по осям, поскольку считается из zoomLevel(); "
        "анизотропный зум в v1 не реализуется"
    )

    viewport_w = viewport_h = None
    if widget is None:
        emit(f"{_FAIL} viewport (привязанный виджет): нет привязанного виджета")
    else:
        viewport_w, width_ok = _probe(widget, "width")
        viewport_h, height_ok = _probe(widget, "height")
        emit(
            f"{_mark(width_ok and height_ok)} viewport (привязанный виджет) = "
            f"{viewport_w}x{viewport_h} px"
        )

    rotation, rotation_ok = _probe(canvas, "rotation")
    emit(f"{_mark(rotation_ok)} Canvas.rotation() = {rotation!r}")
    mirror, mirror_ok = _probe(canvas, "mirror")
    emit(
        f"{_mark(mirror_ok)} Canvas.mirror() = {mirror!r} — принятые формы: "
        "bool (5.3, libkis mirror()/setMirror(bool) = горизонтальное "
        "зеркало), dict {horizontal, vertical}, пара bool"
    )

    doc_w, doc_w_ok = _probe(document, "width")
    doc_h, doc_h_ok = _probe(document, "height")
    orientation = host_view_probe.read_orientation(canvas)
    metrics = None
    if orientation is not None and doc_w_ok and doc_h_ok:
        metrics = host_view_probe.build_metrics(
            widget,
            canvas,
            _number(doc_w) or 0.0,
            _number(doc_h) or 0.0,
            orientation,
        )
    state = to_view_state(metrics)
    if state is None:
        emit(
            f"{_FAIL} вычисленный rect документа не вычисляется "
            "(см. FAIL-строки выше)"
        )
        _host_affine_lines(lines, host_affine)
        return
    rect = state.document_rect_widget
    emit(f"{_OK} view_scale (widget px на doc px) = {view_scale(state)!r}")
    emit(
        f"{_OK} вычисленный rect документа (widget px) = "
        f"x {rect.x!r}, y {rect.y!r}, w {rect.w!r}, h {rect.h!r}"
    )
    _host_affine_lines(lines, host_affine)
    _cross_check_lines(lines, bars, rect)


def _cross_check_lines(lines: List[str], bars, rect) -> None:
    """Compare the computed rect with the host's own scrollbar positions.

    DATA, never a heuristic: nothing here changes the rect, the scale or the
    visibility of the grid — it only measures. ``doc_left = viewport/2 -
    preferredCenter·scale`` is the placement the computed path asserts; the host
    reports its scroll position with the opposite sign, so the comparison is on
    magnitudes, per axis. A zero ``Dx``/``Dy`` means the two independent sources
    agree (the live 5.3.4.1 case: bars -212/-284 against rect 212.0/284.0); a
    LARGE delta means this host's ``zoomLevel()`` means something else — e.g. the
    physical-size mode whose ``zoom·resolution/72`` rule the refuted formula
    assumed — and then the grid is wrong even though every getter answered.
    Without that line a wrong scale rule stays a silently wrong grid; with it one
    report refutes the rule."""
    emit = lines.append
    if not bars:
        emit(
            "  cross-check: пропускается — привязанных скроллбаров нет "
            "(контекст не даёт trigger_status), сверить не с чем"
        )
        return
    by_label = dict(bars)
    values: dict = {}
    for label in ("horizontal", "vertical"):
        value, ok = _slider_number(by_label.get(label), "value")
        if not ok:
            emit(
                f"  {_FAIL} cross-check: scrollbar {label} не отвечает value(): "
                f"{value!r}"
            )
            return
        values[label] = _number(value)
    if values["horizontal"] is None or values["vertical"] is None:
        emit(f"  {_FAIL} cross-check: значения скроллбаров не числа: {values!r}")
        return
    dx = abs(values["horizontal"]) - abs(rect.x)
    dy = abs(values["vertical"]) - abs(rect.y)
    emit(
        f"  scrollbar value: horizontal {values['horizontal']!r}, "
        f"vertical {values['vertical']!r}"
    )
    emit(
        "  cross-check: |scroll value| минус |viewport/2 - preferredCenter*scale| "
        f"-> Dx = {dx:.3f}, Dy = {dy:.3f} (0.000 = масштаб хоста согласован с "
        "вычислением; большое отклонение = другая семантика zoomLevel(), "
        "например physical-size с resolution/72)"
    )


def _conclusion(lines: List[str]) -> None:
    """Close every report with the FIRST broken link, wherever the walk stopped.

    The chain walk returns early by design (there is nothing to learn past a
    missing extension or context), so the summary is emitted at each exit
    instead of only at the end — otherwise the shortest reports, which are the
    most diagnostic, carry no conclusion."""
    lines.append("")
    lines.append("--- вывод по первому оборванному звену ---")
    blocked = [line for line in lines if line.startswith(_FAIL)]
    if not blocked:
        lines.append("  цепочка цела: если сетки всё равно не видно, проблема в отрисовке,")
        lines.append("  а не в привязке — это уровень 3 чек-листа (документ/вид/оверлей).")
    else:
        lines.append(f"  первое FAIL: {blocked[0][len(_FAIL):].strip()}")


def _trigger_wiring_lines(lines: List[str], context) -> None:
    """Print the bind-time repaint-trigger wiring snapshot of a bound context
    plus the scrollbar numbers the live report needs.

    WHY the four bar values: ``minimum/maximum/value/pageStep`` of
    the owning scroll area are the ONLY host-side numbers that independently
    pin the effective zoom and the document's widget-space left edge (the
    computed rect must agree with them on the live screen; disagreement means
    the scale rule or the preferredCenter convention is wrong — exactly what
    one diagnostic run has to settle, and what the ``cross-check`` line of the
    metrics block now measures for itself). The wiring half answers "scroll area
    found? bars wired? viewport filter installed? which event types?" so the
    operator sees the invalidation surface, not only its inputs. All READS."""
    status = getattr(context, "trigger_status", None)
    if not isinstance(status, dict):
        lines.append("  проводка триггеров: контекст не предоставляет trigger_status")
        return
    lines.append("  --- проводка триггеров перерисовки (5.3, без таймеров) ---")
    scroll_area = status.get("scroll_area")
    lines.append(
        f"{_mark(scroll_area is not None)} проводка: scroll area: {_describe(scroll_area)}"
    )
    bars = status.get("bars") or []
    lines.append(
        f"{_mark(bool(bars))} проводка: scrollbars подключено: {len(bars)}"
    )
    for label, bar in zip(("horizontal", "vertical"), bars):
        values: dict = {}
        all_ok = True
        # The METHOD names of QAbstractSlider (minimum/maximum/value/
        # pageStep), each with a property fallback inside _slider_number —
        # ``minimumValue()`` was a Qt *property* name that no binding answers as a
        # method, so PyQt5 turned these four numbers into error text.
        for getter in ("minimum", "maximum", "value", "pageStep"):
            value, ok = _slider_number(bar, getter)
            all_ok = all_ok and ok
            values[getter] = value
        lines.append(
            f"{_mark(all_ok)} scrollbar {label}: min {values['minimum']!r}, "
            f"max {values['maximum']!r}, value {values['value']!r}, "
            f"pageStep {values['pageStep']!r}"
        )
    viewport_names = ", ".join(status.get("viewport_event_names") or ())
    lines.append(
        f"{_mark(bool(status.get('viewport_filter_installed')))} "
        f"проводка: viewport event filter установлен, события: {viewport_names}"
    )
    holder_names = ", ".join(status.get("holder_event_names") or ())
    lines.append(
        f"{_mark(bool(status.get('holder_filter_installed')))} "
        f"проводка: holder event filter установлен, события: {holder_names}"
    )


def _build(lines: List[str], app, extension) -> None:
    def emit(text: str) -> None:
        lines.append(text)

    emit("=== Composition Grid: самодиагностика (только чтение) ===")

    version, version_routes = _krita_version_probe(app)
    emit(f"{_mark(version is not None)} Krita: {version or 'версия не определилась (см. строки ниже)'}")
    for route, value in version_routes:
        emit(f"  {route} = {value}")

    from composition_grid import qt_compat
    from composition_grid.canvas import host_view_probe

    emit(
        f"{_mark(qt_compat.QT_AVAILABLE)} Qt-привязка: "
        + (f"PyQt{qt_compat.qt_major}" if qt_compat.QT_AVAILABLE else "нет (Qt-слой недоступен!)")
    )

    import composition_grid.plugin_composition_grid as entry

    held = list(entry.HELD_PLUGINS)
    if extension is None and not held:
        emit(f"{_FAIL} зарегистрированных расширений (HELD_PLUGINS): 0")
        emit("  -> плагин не создан: либо не включён в Python Plugin Manager,")
        emit("    либо register_plugin() упал при импорте пакета (см. лог ниже).")
        _conclusion(lines)
        return
    emit(
        f"{_mark(bool(held) or extension is not None)} зарегистрированных расширений "
        f"(HELD_PLUGINS): {len(held)}"
    )
    if extension is None:
        extension = held[0]

    document, doc_ok = _probe(app, "activeDocument")
    emit(f"{_mark(doc_ok and document is not None)} activeDocument: {_describe(document)}")
    if doc_ok and document is not None:
        name, _ = _probe(document, "name")
        width, width_ok = _probe(document, "width")
        height, height_ok = _probe(document, "height")
        emit(
            f"{_mark(width_ok and height_ok)} размер документа: "
            f"«{name}» {width}x{height}"
        )
        # The per-document key IS document.name(). The live host answered an
        # EMPTY name for an unsaved document, so routing refused to create a
        # model at all — these lines find a key that actually exists on this
        # host instead of guessing one.
        for candidate in ("name", "fileName", "uniqueId", "title"):
            value, has = _probe(document, candidate)
            emit(f"  {_mark(has)} Document.{candidate}() = {value!r}")
        emit(f"  поверхность Document: {_host_surface(document)}")

    window, win_ok = _probe(app, "activeWindow")
    emit(f"{_mark(win_ok and window is not None)} activeWindow: {_describe(window)}")

    view = None
    qwindow = None
    if window is not None:
        view, view_ok = _probe(window, "activeView")
        emit(f"{_mark(view_ok and view is not None)} Window.activeView(): {_describe(view)}")
        qwindow, _ = _probe(window, "qwindow")
        emit(f"{_mark(qwindow is not None)} Window.qwindow(): {_describe(qwindow)}")

    canvas = None
    if view is not None and not isinstance(view, str):
        canvas, canvas_ok = _probe(view, "canvas")
        emit(f"{_mark(canvas_ok and canvas is not None)} View.canvas(): {_describe(canvas)}")
        view_document, vd_ok = _probe(view, "document")
        emit(
            f"{_mark(vd_ok and view_document is not None)} View.document() "
            f"— источник размера документа: {_describe(view_document)}"
        )

    if canvas is not None and not isinstance(canvas, str):
        for signal_name in (
            "documentRectInWidgetPixelsChanged",
            "rotationChanged",
            "mirrorChanged",
            "sizeChanged",
            "removed",
        ):
            present = hasattr(canvas, signal_name)
            emit(f"{_mark(present)} Canvas.{signal_name} присутствует: {present}")
        if not any(hasattr(canvas, name) for name in ("removed", "sizeChanged")):
            # This block used to conclude «источника нет,
            # view_state не может собраться» — that was wrong here:
            # the ViewState has a computed source and Qt-event invalidation on
            # exactly this host shape. State what actually runs.
            emit(
                "  -> Сигналов Canvas 5.3+ на этом хосте НЕТ: ViewState не"
            )
            emit(
                "     принимается по сигналам, а ВЫЧИСЛЯЕТСЯ из геттеров (см. блок"
            )
            emit(
                "     «метры вида» ниже); перерисовка идёт от Qt-событий вьюпорта"
            )
            emit(
                "     (Paint/Wheel/Resize/скроллбары) и scrollbars-сигналов — без"
            )
            emit(
                "     таймеров и поллинга (глобальное ограничение плана)."
            )
        rect, rect_ok = _probe(canvas, "documentRectInWidgetPixels")
        emit(
            f"{_mark(rect_ok and rect is not None)} "
            f"Canvas.documentRectInWidgetPixels(): {_describe(rect)}"
        )
        # The EFFECTIVE source of the ViewState: which of the
        # paths CanvasContext.view_state defines is actually feeding the
        # overlay right now, and — when the computed one — which inputs the
        # host does not answer.
        host_affine = host_view_probe.read_host_affine(view)
        missing_inputs = [
            name
            for name in ("zoomLevel", "preferredCenter", "rotation", "mirror")
            if not callable(getattr(canvas, name, None))
        ]
        if host_affine is not None:
            emit(
                "  -> источник view_state: аффинное преобразование хоста "
                "(View.flakeToImageTransform + flakeToCanvasTransform) — точен "
                "и при повороте вида; прямоугольник ниже только печатается"
            )
        elif rect_ok and rect is not None:
            emit(
                "  -> источник view_state: точный прямоугольник хоста "
                "(documentRectInWidgetPixels), вычисление не используется"
            )
        else:
            emit(
                "  -> источник view_state: вычисление по геттерам (5.3: точного "
                "прямоугольника нет)"
            )
            if missing_inputs:
                emit(
                    f"     ВНИМАНИЕ: нет геттеров: {', '.join(missing_inputs)} — "
                    "view_state=None, сетка скрыта до ответа хоста"
                )
                emit(
                    "     (деградация); первый такой случай помечен одним INFO"
                )
                emit("     в логе плагина, далее — только DEBUG.")
        # Справка по зафиксированному факту: у libkis Canvas нет размеров
        # документа, и проверка это подтверждает на живом хосте.
        emit(
            "  (справка) getattr(Canvas, 'width') = "
            f"{_describe(getattr(canvas, 'width', None))} — размера документа тут нет"
        )
        emit(f"  поверхность Canvas: {_host_surface(canvas)}")
        # Everything the COMPUTED ViewState is built from, printed as
        # data lines so one live report confirms or refutes the scale
        # relationship. The document the plugin reads the extent through is the
        # one behind the view; the app-level document is the fallback.
        metrics_document = view_document
        if metrics_document is None or isinstance(metrics_document, str):
            metrics_document = document if not isinstance(document, str) else None
        emit("  --- метры вида, из которых считается ViewState (5.3) ---")
        _computed_metrics_lines(
            lines,
            canvas,
            metrics_document,
            _attached_widget(extension),
            _scrollbar_pairs(extension),
            host_affine,
        )
    if view is not None and not isinstance(view, str):
        emit(f"  поверхность View:  {_host_surface(view)}")

    service = extension.service
    grids = service.grids()
    selected = service.selected()
    emit(
        f"{_mark(service.active_doc_id is not None)} service.active_doc_id: "
        f"{service.active_doc_id!r}"
    )
    summary = ", ".join(f"{grid.grid_id}({grid.label or '-'})" for grid in grids)
    emit(
        f"{_mark(bool(grids))} сеток в активном документе: {len(grids)}"
        + (f" -> {summary}" if summary else "")
    )
    if not grids:
        emit("  -> сеток нет ни одной: Tools ▸ Scripts ▸ Add Composition Grid")
    elif selected is None:
        emit("  -> выбранной сетки нет (список есть, selection=None)")
    else:
        emit(
            f"{_mark(True)} выбранная сетка: {selected.grid_id} "
            f"label={selected.label!r}"
        )

    contexts = tuple(extension.contexts)
    emit(f"{_mark(bool(contexts))} контекстов окна: {len(contexts)}")
    if not contexts:
        emit("  -> ни одного CanvasContext: createActions(window) не выполнялся,")
        emit("    либо окно не разрешилось в момент вызова.")
        _conclusion(lines)
        return

    context = contexts[0]
    widget = getattr(context, "widget", None)
    emit(f"{_mark(widget is not None)} context.widget (привязанный виджет): {_describe(widget)}")
    # Which Canvas the context is actually attached to:
    # compare with ``View.canvas()`` above — a different identity after a tab
    # switch means the rebind did NOT happen, which was previously invisible.
    attached_canvas = getattr(context, "canvas", _ABSENT)
    if attached_canvas is _ABSENT:
        emit("  context.canvas: контекст не предоставляет (вне привязки?)")
    else:
        emit(
            f"{_mark(attached_canvas is not None)} "
            f"context.canvas (привязанный Canvas): {_describe(attached_canvas)}"
        )
    if widget is not None:
        from composition_grid.canvas.widget_plausibility import is_plausible_canvas_widget

        plausible, reason = is_plausible_canvas_widget(widget)
        emit(f"{_mark(plausible)} plausibility-вердикт по нему: {plausible} {reason!r}")
        try:
            emit(f"  геометрия виджета: {widget.width()}x{widget.height()} px")
        except Exception as exc:  # noqa: BLE001
            emit(f"  FAIL чтение геометрии виджета: {exc!r}")
        _trigger_wiring_lines(lines, context)

    if qwindow is not None:
        from composition_grid.canvas.canvas_locator import find_canvas_widget

        located = find_canvas_widget(qwindow)
        emit(f"{_mark(located is not None)} find_canvas_widget(qwindow): {_describe(located)}")
        if located is not None and widget is not None and located is not widget:
            emit("  ВНИМАНИЕ: locator сейчас отвечает ДРУГОЙ виджет, чем привязано в контексте")

    overlay = getattr(context, "overlay", None)
    emit(f"{_mark(overlay is not None)} context.overlay: {_describe(overlay)}")
    if overlay is not None:
        try:
            geometry = overlay.geometry()
            transparent = bool(overlay.testAttribute(qt_compat.WA_TransparentForMouseEvents))
            emit(
                f"  overlay: {geometry.width()}x{geometry.height()} px, "
                f"isVisible={overlay.isVisible()}, "
                f"WA_TransparentForMouseEvents={transparent}, "
                f"parent={_describe(overlay.parentWidget())}"
            )
        except Exception as exc:  # noqa: BLE001
            emit(f"  FAIL чтение состояния overlay: {exc!r}")

    view_state = getattr(context, "view_state", None)
    emit(f"{_mark(view_state is not None)} context.view_state: {_describe(view_state)}")
    if view_state is not None:
        rect = view_state.document_rect_widget
        emit(
            f"  doc {view_state.doc_width}x{view_state.doc_height} -> rect "
            f"{rect.w}x{rect.h} @ ({rect.x}, {rect.y}), rotation {view_state.rotation_deg}"
        )
        if view_state.host_affine is not None:
            emit(
                "  -> маппинг doc->widget: аффинное преобразование хоста "
                "(exact incl. rotation); rect/rotation выше — только печать"
            )
    try:
        doc_size = extension._context_doc_size(context)
    except Exception as exc:  # noqa: BLE001
        doc_size = f"<!{exc!r}>"
    emit(f"{_mark(isinstance(doc_size, tuple))} doc_size провайдер: {doc_size!r}")

    model = selected
    emit(f"{_mark(model is not None)} выбранная сетка: {_describe(model)}")
    if model is not None:
        transform = model.transform
        emit(
            f"  тип={model.grid_type_id} visible={model.visible} "
            f"edit_session={model.edit_session}"
        )
        emit(
            f"  элемент: центр ({transform.cx:.0f}, {transform.cy:.0f}) "
            f"{transform.width:.0f}x{transform.height:.0f} "
            f"поворот {transform.rotation_deg:.0f}°"
        )
        if not model.visible:
            emit("  -> модель есть, но visible=False: скрыть/показать через Visible")
        if view_state is None:
            emit("  -> модель есть, но view_state=None: оверлею некуда маппить")

    _conclusion(lines)


def write_report(text: str, path: Path = REPORT_PATH) -> Path:
    """Persist the report next to the dialog so it can be attached, not typed."""
    path.write_text(text, encoding="utf-8")
    return path
