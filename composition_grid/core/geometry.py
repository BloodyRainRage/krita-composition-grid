"""2x3 affine matrix and rectangular bounds in normalized/document space.

Pure math core: no Qt, no krita — only stdlib.

Coordinate convention: document space with y down (screen-style).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True)
class Affine:
    """2x3 affine matrix, SVG-style.

    Maps (x, y) -> (a*x + c*y + e, b*x + d*y + f).
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def apply(self, p: Point) -> Point:
        x, y = p
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def composed(self, other: "Affine") -> "Affine":
        """Apply ``self`` first, then ``other`` (function-composition order)."""
        # other ∘ self
        a = self.a * other.a + self.b * other.c
        b = self.a * other.b + self.b * other.d
        c = self.c * other.a + self.d * other.c
        d = self.c * other.b + self.d * other.d
        e = self.e * other.a + self.f * other.c + other.e
        f = self.e * other.b + self.f * other.d + other.f
        return Affine(a=a, b=b, c=c, d=d, e=e, f=f)

    @classmethod
    def identity(cls) -> "Affine":
        return cls(a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0)

    @classmethod
    def translation(cls, dx: float, dy: float) -> "Affine":
        return cls(a=1.0, b=0.0, c=0.0, d=1.0, e=float(dx), f=float(dy))

    @classmethod
    def scaling(cls, sx: float, sy: float) -> "Affine":
        return cls(a=float(sx), b=0.0, c=0.0, d=float(sy), e=0.0, f=0.0)

    @classmethod
    def rotation_deg(cls, angle_deg: float) -> "Affine":
        """CCW rotation in standard math orientation.

        Signed sine in the b-component: a=cos, b=sin, c=-sin, d=cos.
        """
        rad = math.radians(angle_deg)
        cos_v = math.cos(rad)
        sin_v = math.sin(rad)
        return cls(a=cos_v, b=sin_v, c=-sin_v, d=cos_v, e=0.0, f=0.0)

    @classmethod
    def mirror(cls, flip_x: bool, flip_y: bool) -> "Affine":
        sx = -1.0 if flip_x else 1.0
        sy = -1.0 if flip_y else 1.0
        return cls(a=sx, b=0.0, c=0.0, d=sy, e=0.0, f=0.0)

    def inverted(self) -> "Affine":
        """Analytic 2x3 inverse: ``self.inverted().apply(self.apply(p)) == p``.

        Requires a nonzero determinant (no degenerate scales).
        """
        det = self.a * self.d - self.b * self.c
        if det == 0.0:
            raise ValueError("cannot invert a degenerate (singular) Affine")
        a = self.d / det
        b = -self.b / det
        c = -self.c / det
        d = self.a / det
        e = -(self.d * self.e - self.c * self.f) / det
        f = (self.b * self.e - self.a * self.f) / det
        return Affine(a=a, b=b, c=c, d=d, e=e, f=f)


@dataclass(frozen=True)
class Bounds:
    """Axis-aligned rectangle: origin (x, y), size (w, h)."""

    x: float
    y: float
    w: float
    h: float

    def center(self) -> Point:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def corners(self) -> tuple[Point, Point, Point, Point]:
        """(nw, ne, se, sw)."""
        left, top = self.x, self.y
        right, bottom = self.x + self.w, self.y + self.h
        return (
            (left, top),
            (right, top),
            (right, bottom),
            (left, bottom),
        )

    def midpoints(self) -> dict[str, Point]:
        cx, cy = self.center()
        return {
            "n": (cx, self.y),
            "e": (self.x + self.w, cy),
            "s": (cx, self.y + self.h),
            "w": (self.x, cy),
        }

    def normalized(self, p: Point) -> Point:
        """Map an absolute point to 0..1 relative to the bounds."""
        return ((p[0] - self.x) / self.w, (p[1] - self.y) / self.h)

    def scaled(self, f: float) -> "Bounds":
        """Scale sizes by ``f`` about the same center."""
        new_w = self.w * f
        new_h = self.h * f
        cx, cy = self.center()
        return Bounds(x=cx - new_w / 2.0, y=cy - new_h / 2.0, w=new_w, h=new_h)

    def shrunken(self, x: float, y: float) -> "Bounds":
        """Shrink by inset amounts ``x`` (horizontal) and ``y`` (vertical) on each side."""
        return Bounds(
            x=self.x + x,
            y=self.y + y,
            w=self.w - 2.0 * x,
            h=self.h - 2.0 * y,
        )
