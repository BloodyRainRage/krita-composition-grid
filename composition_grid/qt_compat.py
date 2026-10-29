"""Single PyQt5/PyQt6 normalization layer for the composition_grid plugin.

WHY this module exists
======================

Every other module in the plugin must import Qt *only* from here. PyQt5 and
PyQt6 differ in three volatile ways: the enum access style (`Qt.SolidLine`
vs. the scoped `Qt.PenStyle.SolidLine`), which top-level package name the
binding lives under (`PyQt5` vs `PyQt6`), and minor attribute placements
(`Signal` lives in `QtCore` in both, but consumers should not have to know
or care). Depending on the raw binding directly would couple every consumer
to these volatile details; this module instead provides a small, STABLE
interface (SOLID's dependency inversion applied to Qt bindings): consumers
depend on frozen names defined once here, and only this file changes if the
plugin ever gains a third binding (PySide) or a Qt7.

Behaviour contract
==================

- Importing this module in a *pure* environment (no PyQt at all) is NOT an
  error: ``QT_AVAILABLE`` is False, ``qt_major`` is 0, and the Qt names are
  simply absent so tests can skip instead of crashing.
- When PyQt is present, the module exports ``QtCore``, ``QtGui``,
  ``QtWidgets``, ``Qt = QtCore.Qt``, ``Signal = QtCore.Signal`` and the
  normalized scalars resolved through :func:`_pick` (Qt6-scoped paths first,
  Qt5 flat paths as fallback; NB: PyQt exposes ``pyqtSignal`` — the flat
  ``Signal`` name is PySide's — and Qt6 renamed ``MousePress``/``MouseRelease``
  to ``MouseButtonPress``/``MouseButtonRelease``; the normalized EXPORT names
  stay stable, only the resolvable path maps per binding). The exported QEvent
  set is ``MousePress``, ``MouseMove``, ``MouseRelease``, ``Enter``, ``Leave``,
  ``Resize``, ``Close``, ``Destroy``, ``Show``, ``Hide`` plus ``Wheel``,
  ``Scroll``, ``ScrollPrepare``, ``Paint`` and ``LayoutRequest`` (the last
  five added for the Krita-5.3 repaint triggers). The
  docker color dialog adds ``ShowAlphaChannel`` (QColorDialog option) and
  ``DialogAccepted`` (QDialog result code); the docker grid list adds
  ``UserRole``, the three ``ItemIs*`` flags, ``SingleSelection``, the
  ``DoubleClicked``/``EditKeyPressed`` edit triggers, ``LeftButton`` (the eye
  toggle hit test) and the ``P_Text``/``P_WindowText`` palette roles the dimmed
  row text is read and written through.
- If a named constant cannot be resolved even though Qt IS available, the
  import raises ``ImportError`` naming the constant — we fail loudly and
  never silently export a wrong value. The sole exception is
  ``QEvent.Type.Destroy`` under Qt6 (removed from the public enum):
  exported as ``None`` with a dialog-loud WARNING at import; consumers must
  guard (prefer the underlying ``destroyed()`` signal / ``DeferredDelete``)
  — an honest absent sentinel, not a silent wrong value.
"""

import importlib
import logging

logger = logging.getLogger("composition_grid")

QT_AVAILABLE: bool = False
qt_major: int = 0


def _pick(*paths):
    """Return the value of the first attribute path in *paths* that exists.

    Each path is a dotted string such as ``"Qt.PenStyle.SolidLine"`` where the
    first segment names one of the imported Qt modules (``QtCore``, ``QtGui``,
    ``QtWidgets``, ``Qt``). Qt6-style enum-scoped paths must be listed before
    the Qt5-style flat paths so behaviour is deterministic per binding.

    Raises ``ImportError`` naming every candidate if none resolves — never
    silently exports a stand-in value.
    """
    for path in paths:
        head, *attrs = path.split(".")
        obj = globals().get(head)
        if obj is None:
            # head is not one of the exported Qt modules; skip quietly but
            # trace it, since it usually means a path was spelled wrong.
            logger.debug("qt_compat._pick: no module object %r for path %r", head, path)
            continue
        try:
            for attr in attrs:
                obj = getattr(obj, attr)
        except AttributeError:
            continue
        return obj  # first existing path wins → deterministic binding choice
    raise ImportError(
        "composition_grid.qt_compat: cannot resolve constant on PyQt%d; tried: %s"
        % (qt_major, " | ".join(repr(p) for p in paths))
    )


def _import_qt():
    """Import one Qt binding. Tries PyQt6 first, then PyQt5 (same order as
    tests/conftest.try_import_qt). Returns ``(major, QtCore, QtGui, QtWidgets)``
    or ``(0, None, None, None)`` when no binding is importable.

    Only ``ImportError`` means "not installed"; anything else is logged at
    warning level and treated as unavailable for that binding.
    """
    for major in (6, 5):
        try:
            QtCore = importlib.import_module(f"PyQt{major}.QtCore")
            QtGui = importlib.import_module(f"PyQt{major}.QtGui")
            QtWidgets = importlib.import_module(f"PyQt{major}.QtWidgets")
        except ImportError as exc:
            # expected in pure environments (e.g. CI without PyQt)
            logger.debug("PyQt%d not importable: %s", major, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — defend the plugin host (no Qt import crash loops)
            logger.warning("PyQt%d import failed unexpectedly: %r", major, exc)
            continue
        return major, QtCore, QtGui, QtWidgets
    return 0, None, None, None


_qt_major, QtCore, QtGui, QtWidgets = _import_qt()
QT_AVAILABLE = _qt_major != 0
qt_major = _qt_major

if not QT_AVAILABLE:
    # Pure environment: keep the other exported names absent on purpose.
    del QtCore, QtGui, QtWidgets
else:
    Qt = QtCore.Qt  # namespace base shared by all consumers
    Signal = _pick(
        "QtCore.Signal",
        "QtCore.pyqtSignal",
    )  # PyQt6/PyQt5 expose pyqtSignal; the flat `Signal` name is PySide's

    # --- QEvent types (Qt6: scoped under QEvent.Type; Qt5: flat on QEvent).
    # NB: Qt6 RENAMED the press/release values (Qt5 MousePress ->
    # Qt6 MouseButtonPress); the exported scalar names stay MousePress/
    # MouseRelease for consumers and are resolved to the binding's member.
    MousePress = _pick(
        "QtCore.QEvent.Type.MouseButtonPress",
        "QtCore.QEvent.MousePress",
    )
    MouseMove = _pick(
        "QtCore.QEvent.Type.MouseMove",
        "QtCore.QEvent.MouseMove",
    )
    MouseRelease = _pick(
        "QtCore.QEvent.Type.MouseButtonRelease",
        "QtCore.QEvent.MouseRelease",
    )
    Enter = _pick("QtCore.QEvent.Type.Enter", "QtCore.QEvent.Enter")
    Leave = _pick("QtCore.QEvent.Type.Leave", "QtCore.QEvent.Leave")
    Resize = _pick("QtCore.QEvent.Type.Resize", "QtCore.QEvent.Resize")
    Close = _pick("QtCore.QEvent.Type.Close", "QtCore.QEvent.Close")
    Destroy = None
    try:
        Destroy = _pick("QtCore.QEvent.Type.Destroy", "QtCore.QEvent.Destroy")
    except ImportError:
        # Qt6 removed QEvent.Type.Destroy from the public enum (2023 6.x
        # bindings). Export None + a LOUD warning: an absent value is honest
        # (callers must guard and prefer the `destroyed()` signal or
        # DeferredDelete), a silent stand-in value is not. PyQt5 keeps the
        # real value.
        logger.warning(
            "qt_compat: QEvent.Type.Destroy is not exposed by PyQt6 "
            "(removed from the public Qt6 enum); exporting Destroy=None — "
            "consumers must guard (prefer destroyed() signal / DeferredDelete)"
        )
    Show = _pick("QtCore.QEvent.Type.Show", "QtCore.QEvent.Show")
    Hide = _pick("QtCore.QEvent.Type.Hide", "QtCore.QEvent.Hide")

    # --- The Krita 5.3 repaint triggers ------------------------------------
    # On 5.3.x libkis Canvas exposes NO signals, so the only genuine
    # invalidation sources left are Qt's own viewport events: the wheel is how
    # the user zooms, and Scroll/ScrollPrepare are what a scroll area emits
    # around a scroll (QScrollArea-based canvas holder). They join Resize,
    # which is already above. Two more are needed: Paint — the
    # viewport must repaint for changes that move no scrollbar at all (mirror,
    # menu/N-key rotation, slider zoom with range 0..0, zoom-to-cursor) — and
    # LayoutRequest — the scroll-area holder's signal for the resetCanvas
    # widget swap re-laying it out.
    Wheel = _pick("QtCore.QEvent.Type.Wheel", "QtCore.QEvent.Wheel")
    Scroll = _pick("QtCore.QEvent.Type.Scroll", "QtCore.QEvent.Scroll")
    ScrollPrepare = _pick(
        "QtCore.QEvent.Type.ScrollPrepare", "QtCore.QEvent.ScrollPrepare"
    )
    Paint = _pick("QtCore.QEvent.Type.Paint", "QtCore.QEvent.Paint")
    LayoutRequest = _pick(
        "QtCore.QEvent.Type.LayoutRequest", "QtCore.QEvent.LayoutRequest"
    )

    # --- Widget attributes (note: WA_NoSystemBackground is the correct Qt name) ---
    WA_TransparentForMouseEvents = _pick(
        "QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents",
        "QtCore.Qt.WA_TransparentForMouseEvents",
    )
    WA_TranslucentBackground = _pick(
        "QtCore.Qt.WidgetAttribute.WA_TranslucentBackground",
        "QtCore.Qt.WA_TranslucentBackground",
    )
    WA_NoSystemBackground = _pick(
        "QtCore.Qt.WidgetAttribute.WA_NoSystemBackground",
        "QtCore.Qt.WA_NoSystemBackground",
    )

    # --- Pen styles (Qt6: QtGui.QPainter.PenStyle.*; Qt5: flat on the Qt namespace / QPainter) ---
    SolidLine = _pick(
        "QtGui.QPainter.PenStyle.SolidLine",
        "QtGui.QPainter.SolidLine",
        "QtCore.Qt.PenStyle.SolidLine",
        "QtCore.Qt.SolidLine",
    )
    CustomDashLine = _pick(
        "QtGui.QPainter.PenStyle.CustomDashLine",
        "QtGui.QPainter.CustomDashLine",
        "QtCore.Qt.PenStyle.CustomDashLine",
        "QtCore.Qt.CustomDashLine",
    )

    # --- Cursor shapes (Qt6: Qt.CursorShape.*; Qt5: flat Qt.*Cursor) ---
    CrossCursor = _pick("QtCore.Qt.CursorShape.CrossCursor", "QtCore.Qt.CrossCursor")
    SizeAllCursor = _pick("QtCore.Qt.CursorShape.SizeAllCursor", "QtCore.Qt.SizeAllCursor")
    SizeHorCursor = _pick("QtCore.Qt.CursorShape.SizeHorCursor", "QtCore.Qt.SizeHorCursor")
    SizeVerCursor = _pick("QtCore.Qt.CursorShape.SizeVerCursor", "QtCore.Qt.SizeVerCursor")
    SizeBDiagCursor = _pick("QtCore.Qt.CursorShape.SizeBDiagCursor", "QtCore.Qt.SizeBDiagCursor")
    SizeFDiagCursor = _pick("QtCore.Qt.CursorShape.SizeFDiagCursor", "QtCore.Qt.SizeFDiagCursor")
    OpenHandCursor = _pick("QtCore.Qt.CursorShape.OpenHandCursor", "QtCore.Qt.OpenHandCursor")
    ClosedHandCursor = _pick("QtCore.Qt.CursorShape.ClosedHandCursor", "QtCore.Qt.ClosedHandCursor")
    ArrowCursor = _pick("QtCore.Qt.CursorShape.ArrowCursor", "QtCore.Qt.ArrowCursor")
    PointingHandCursor = _pick("QtCore.Qt.CursorShape.PointingHandCursor", "QtCore.Qt.PointingHandCursor")

    # --- QColorDialog option + QDialog result (Qt6: scoped; Qt5: flat) ---
    ShowAlphaChannel = _pick(
        "QtWidgets.QColorDialog.ColorDialogOption.ShowAlphaChannel",
        "QtWidgets.QColorDialog.ShowAlphaChannel",
    )
    DialogAccepted = _pick(
        "QtWidgets.QDialog.DialogCode.Accepted",
        "QtWidgets.QDialog.Accepted",
    )

    # --- Alignment / keys ---
    AlignCenter = _pick(
        "QtCore.Qt.AlignmentFlag.AlignCenter",
        "QtCore.Qt.AlignCenter",
    )
    Key_Escape = _pick(
        "QtCore.Qt.Key.Key_Escape",
        "QtCore.Qt.Key_Escape",
    )

    # --- Item-view constants (docker grid list; Qt6: scoped, Qt5: flat) -------
    # ``QListWidget`` rows carry their grid id in ``UserRole``, are enabled,
    # selectable and editable (rename), and the view is single-selection with
    # double-click / F2 as the only edit triggers. ``LeftButton`` is the mouse
    # button the row's eye toggle hit test answers to (visibility has no Qt
    # check state any more, so the click must be resolved by geometry).
    UserRole = _pick("QtCore.Qt.ItemDataRole.UserRole", "QtCore.Qt.UserRole")
    ItemIsEnabled = _pick(
        "QtCore.Qt.ItemFlag.ItemIsEnabled", "QtCore.Qt.ItemIsEnabled"
    )
    ItemIsSelectable = _pick(
        "QtCore.Qt.ItemFlag.ItemIsSelectable", "QtCore.Qt.ItemIsSelectable"
    )
    ItemIsEditable = _pick(
        "QtCore.Qt.ItemFlag.ItemIsEditable", "QtCore.Qt.ItemIsEditable"
    )
    LeftButton = _pick(
        "QtCore.Qt.MouseButton.LeftButton", "QtCore.Qt.LeftButton"
    )
    P_Text = _pick(
        "QtGui.QPalette.ColorRole.Text", "QtGui.QPalette.Text"
    )
    P_WindowText = _pick(
        "QtGui.QPalette.ColorRole.WindowText", "QtGui.QPalette.WindowText"
    )
    SingleSelection = _pick(
        "QtWidgets.QAbstractItemView.SelectionMode.SingleSelection",
        "QtWidgets.QAbstractItemView.SingleSelection",
    )
    DoubleClicked = _pick(
        "QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked",
        "QtWidgets.QAbstractItemView.DoubleClicked",
    )
    EditKeyPressed = _pick(
        "QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed",
        "QtWidgets.QAbstractItemView.EditKeyPressed",
    )
