#!/bin/sh
# Install the «Composition Grid» Krita plugin into a pykrita directory.
#
# POSIX sh counterpart of tools/install.ps1: it copies exactly
# composition_grid.desktop and the composition_grid/ package (source files
# only) into the target pykrita directory, creating directories as needed.
#
#   sh tools/install.sh                      # $HOME/.local/share/krita/pykrita
#   sh tools/install.sh /path/to/pykrita     # Flatpak profile, portable, ...
#
# Run it with `sh`, not as ./tools/install.sh: the executable bit is not
# guaranteed after a git clone on Windows or a zip download.
#
# Safety: nothing is ever deleted — neither in the target directory nor
# outside it. Only the plugin's own files are overwritten, so neighbouring
# plugins in pykrita stay untouched. Consequently, files of an OLDER plugin
# version that no longer exist in this package stay behind and remain
# importable; remove them by hand when upgrading.

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DESKTOP_FILE=composition_grid.desktop
PACKAGE_DIR=composition_grid

# Krita's default resource location on Linux; Flatpak installs use
# $HOME/.var/app/org.kde.krita/data/krita/pykrita.
DESTINATION=${1:-"$HOME/.local/share/krita/pykrita"}

case $# in
    0 | 1) ;;
    *)
        echo "usage: sh tools/install.sh [pykrita-directory]" >&2
        exit 2
        ;;
esac

if [ ! -f "$REPO_ROOT/$DESKTOP_FILE" ]; then
    echo "install failed: plugin registration file not found: $REPO_ROOT/$DESKTOP_FILE" >&2
    exit 1
fi
if [ ! -d "$REPO_ROOT/$PACKAGE_DIR" ]; then
    echo "install failed: plugin package directory not found: $REPO_ROOT/$PACKAGE_DIR" >&2
    exit 1
fi

echo "source      : $REPO_ROOT"
echo "destination : $DESTINATION"
echo ""

mkdir -p "$DESTINATION"

copied=1
echo "  + $DESKTOP_FILE"
cp "$REPO_ROOT/$DESKTOP_FILE" "$DESTINATION/$DESKTOP_FILE"

# Build noise never installs: caches and compiled artifacts. find prints paths
# relative to the package directory, so the same layout is recreated below.
cd "$REPO_ROOT/$PACKAGE_DIR"
while IFS= read -r relative; do
    target_dir="$DESTINATION/$PACKAGE_DIR/$(dirname -- "$relative")"
    mkdir -p "$target_dir"
    cp "$REPO_ROOT/$PACKAGE_DIR/$relative" "$DESTINATION/$PACKAGE_DIR/$relative"
    copied=$((copied + 1))
    echo "  + $PACKAGE_DIR/$relative"
done <<EOF
$(find . -type f \
    ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' \
    ! -name '.DS_Store' | sed 's|^\./||' | LC_ALL=C sort)
EOF

echo ""
echo "copied files: $copied ($DESKTOP_FILE + the $PACKAGE_DIR package)."

if [ ! -f "$DESTINATION/$PACKAGE_DIR/__init__.py" ]; then
    echo "warning: the installed package has no __init__.py — Krita cannot import it." >&2
    echo "         run the script from the repository root." >&2
    exit 1
fi

echo ""
echo "Required steps after copying:"
echo "  1. In Krita: Settings > Configure Krita... > Python Plugin Manager,"
echo "     enable «Composition Grid», OK."
echo "  2. RESTART Krita: without a restart the Extension is not created, so"
echo "     neither the menu commands (Tools > Scripts) nor the docker appear."
echo "  3. Enable the panel in Settings > Dockers: it is registered as"
echo "     composition_grid_docker."
echo ""
echo "To uninstall: uncheck the plugin in Python Plugin Manager and, if needed,"
echo "remove $DESKTOP_FILE and $PACKAGE_DIR/ from $DESTINATION by hand"
echo "(this script deletes nothing)."
