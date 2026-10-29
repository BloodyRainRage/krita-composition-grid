"""Style dataclass for grid primitives, with value clamping.

Pure math/config core: no Qt, no krita — only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass

DashPattern = tuple[float, ...]


@dataclass(frozen=True)
class Style:
    color_rgba: tuple[float, float, float, float] = (0.9, 0.9, 0.9, 0.8)
    width_px: float = 1.0
    opacity: float = 0.8
    dash: DashPattern | None = None

    def __post_init__(self) -> None:
        # frozen dataclass: mutate values via object.__setattr__ during init only.
        object.__setattr__(self, "width_px", _clamp(self.width_px, 0.25, 12.0))
        object.__setattr__(self, "opacity", _clamp(self.opacity, 0.0, 1.0))
        object.__setattr__(
            self,
            "color_rgba",
            tuple(_clamp(c, 0.0, 1.0) for c in self.color_rgba),
        )
        # ``dash`` crosses the JSON boundary (settings_store round-trips it via
        # ``asdict``), which turns the pattern tuple into a list; every consumer
        # (the docker dash combo, the overlay painter) compares against the
        # pinned tuple presets, so normalize any non-None sequence back to a
        # tuple here — the single root, so no downstream module re-does it.
        if self.dash is not None:
            object.__setattr__(self, "dash", tuple(self.dash))


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
