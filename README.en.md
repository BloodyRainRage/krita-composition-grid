[Русский](README.md) | **English**

# Composition Grid — a composition grid overlay for Krita

A transformable template laid over the Krita canvas: move, scale and rotate a
composition grid without touching the document itself.

[![CI](https://img.shields.io/github/actions/workflow/status/BloodyRainRage/krita-composition-grid/ci.yml?branch=main&label=CI)](https://github.com/BloodyRainRage/krita-composition-grid/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/BloodyRainRage/krita-composition-grid?label=release)](https://github.com/BloodyRainRage/krita-composition-grid/releases/latest)
![Krita 5.3+](https://img.shields.io/badge/Krita-5.3%2B-informational)
![License GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

<p align="center"><img src="docs/images/canvas.png" alt="A Diagonal Method grid on the Krita canvas in edit mode, with the Composition Grid docker" width="800"></p>

## Features

- **12 grid types**: rule of thirds and fifths, golden ratio, diagonal method,
  dynamic symmetry, golden spiral and golden triangles, perspective and more —
  each with its own parameters.
- **Several grids per document**: a list in the docker, clicking a row edits
  that grid, the eye shows/hides it, double-click renames it.
- **Direct manipulation on the canvas**: drag anywhere inside the grid to move
  it, resize by corners and edges, rotate by the marker above the top edge, and
  drag the vanishing point for the perspective grid.
- **Non-destructive by construction**: the grid is a transparent widget *above*
  the viewport, not a layer. It never reaches the export, never changes pixels
  and never gets in the way of drawing while the edit session is off.
- **State lives inside the `.kra` file** as a document annotation: open a
  document and all its grids are exactly where you left them; opening does not
  mark the file modified (editing a grid does, so it can be saved).
- **Windows and Linux**, Krita 5.3 (PyQt5); the code is PyQt6-compatible too.
  **871 automated tests**, built-in "why can't I see the grid" diagnostics.

## Requirements

| Component | Requirement |
|---|---|
| Krita | **5.3+** (verified live on 5.3.1 on Windows; Krita 6.x untested) |
| Python inside Krita | 3.10–3.13 (bundled with Krita; nothing to install separately) |
| Dependencies | none: only what Krita already provides (libkis + a Qt binding) |

The plugin is platform-independent. Live verification in Krita was done on
Windows; on Linux the automated tests run in CI, a live Krita run on Linux and
macOS has not been done yet.

## Installation

The easiest path is the prebuilt archive from Releases.

1. Download `composition_grid-<version>.zip` from the
   [latest release page](https://github.com/BloodyRainRage/krita-composition-grid/releases/latest).
2. In Krita: **Tools ▸ Scripts ▸ Import Python Plugin from File…** and pick the
   archive.
3. **Settings ▸ Configure Krita… ▸ Python Plugin Manager**: enable
   **Composition Grid**, OK.
4. **Restart Krita** — without a restart neither the docker nor the menu
   commands appear.
5. Open the panel: **Settings ▸ Dockers ▸ Composition Grid**.

### Copying manually

You can also unpack the archive (or the folder from it) by hand:
`composition_grid.desktop` and the `composition_grid/` folder must sit **side by
side** in the `pykrita` root:

| OS | `pykrita` directory |
|---|---|
| Windows | `%APPDATA%\krita\pykrita\` |
| Linux (deb/AppImage) | `~/.local/share/krita/pykrita/` |
| Linux (Flatpak) | `~/.var/app/org.kde.krita/data/krita/pykrita/` |
| macOS (untested) | `~/Library/Application Support/krita/pykrita/` |

### After installation

The plugin's commands live in **Tools ▸ Scripts**: *Add Composition Grid*,
*Toggle Composition Grid*, *Remove Composition Grid*, *Toggle Grid Edit
Session*, *Reset Grid Transform*, *Cycle Grid Type*. No shortcuts are assigned
by default — set them under **Settings ▸ Configure Krita… ▸ Keyboard Shortcuts**.

## Usage

<img src="docs/images/docker.png" alt="Composition Grid docker: grid list with eye toggles, parameters and style" width="300" align="right">

- **Add grid** adds a grid to the current document (or use **Tools ▸ Scripts ▸
  Add Composition Grid**). A new blank canvas holds no grids at all: a grid
  appears only when you ask for one.
- **Click a row in the list** selects that grid and opens it on the canvas for
  editing — the markers appear without any extra button.
- **Drag inside the grid** to move it; **corner and edge markers** resize it;
  the **marker above the top middle** rotates it around its centre.
- **Shift** while dragging a corner temporarily inverts "Lock Ratio".
- The **eye** left of a row shows/hides that grid (a hidden grid stays in the
  document); **Remove** deletes the selected grid together with its parameters.
- **Double-click** (or F2) a row to rename its label.
- **Leaving the edit session**: click outside the grid and its markers, press
  "Edit Grid on Canvas" again, or run *Toggle Grid Edit Session*. Outside the
  session the grid swallows no clicks at all, so drawing works as usual.
- Style (colour, width, opacity, dash) and the type's parameters are in the
  docker and apply immediately. Line width is given in screen pixels, so it does
  not change with zoom.
- Everything is stored **inside the `.kra`**: a saved file opens with the same
  grid list, selection and transforms.

<br clear="right">

## The 12 grid types

| Name in the docker | What it draws |
|---|---|
| **Rule of Thirds** | two verticals and two horizontals at 1/3 and 2/3, plus the four power points |
| **Center Cross** | a centre cross; the vertical, the horizontal and the centre dot can be switched on separately |
| **Quadrant Grid** | a centre cross plus, optionally, four dots in the centres of the quadrants |
| **Golden Ratio (Phi)** | lines at 1/φ and 1−1/φ on both axes and their four intersections |
| **Rule of Fifths** | four verticals and four horizontals dividing the element into five equal strips |
| **Uniform Grid** | an even grid: `columns` × `rows` (1…24) with a `margin` parameter |
| **Symmetry Axes** | 1…4 vertical and 1…4 horizontal axes, mirrored about the centre |
| **Golden Spiral** | a spiral of six golden rectangles fitted to the whole frame; the starting corner is `origin` |
| **Golden Triangles** | a diagonal plus the perpendiculars from the two vertices that are not on it |
| **Diagonal Method** | diagonals out of all four corners plus 45° rays from the midpoints of the long sides |
| **Dynamic Symmetry** | a root rectangle √2/√3/√5, its sides, diagonals and Hambidge reciprocals |
| **Perspective** | one vanishing point, rays over half a turn through it, and the horizon |

## Building from source

[uv](https://docs.astral.sh/uv/) is required; using the plugin inside Krita
needs no build at all.

```powershell
# Windows (PowerShell)
git clone https://github.com/BloodyRainRage/krita-composition-grid.git
cd krita-composition-grid
uv sync
uv run python tools\build_plugin_zip.py
uv run pytest -q
```

```sh
# Linux / macOS
git clone https://github.com/BloodyRainRage/krita-composition-grid.git
cd krita-composition-grid
uv sync
uv run python tools/build_plugin_zip.py
uv run pytest -q
```

`uv sync` installs the dev dependencies (`pytest` and `PyQt6`), so the Qt tests
run instead of being skipped; the artifact lands in
`dist/composition_grid-<version>.zip`. The tests need `QT_QPA_PLATFORM=offscreen`
(set automatically in CI, and by part of the Qt test modules themselves).

### Installing from source without building an archive

```powershell
# Windows: default target — %APPDATA%\krita\pykrita
powershell -ExecutionPolicy Bypass -File .\tools\install.ps1
```

```sh
# Linux: default target — $HOME/.local/share/krita/pykrita
sh tools/install.sh

# Flatpak Krita profile — pass the target as the first argument
sh tools/install.sh "$HOME/.var/app/org.kde.krita/data/krita/pykrita"
```

Both scripts copy exactly `composition_grid.desktop` and the
`composition_grid/` package (no `__pycache__`, no `*.pyc`), delete nothing and
print the list of files they copied. Afterwards — enable the plugin in the
Python Plugin Manager and restart Krita. When upgrading in place, orphaned files
of the old version must be removed by hand: the scripts deliberately delete
nothing.

## License

GPL-3.0-or-later. The full text is in [`LICENSE`](LICENSE).
