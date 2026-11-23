"""Sky geometry and weather maths of the preview -- pure numpy/Python, no GL.

Everything here follows the decompiled builders (ATM10 notes in ATM1):

  * dome    `FUN_00460ec0` / `FUN_00460f40`: a 16x16 vertex GRID of side
            `dome_radius` (not a disc), `y = height - curvature*(a^2 + b^2)`,
            rotated by `angle` and multiplied by `scale`;
  * horizon `FUN_00459d30`: a 16-sided ring of fixed radius 400, from
            `horizon_bottom` up to `horizon_bottom + horizon_top`;
  * angles are in TURNS (the game's sin/cos helpers multiply by 2*pi).

Coordinates come out in GAME space (left-handed); the renderer mirrors Z.
"""

import math

import numpy as np

TURN = 2.0 * math.pi
GRID = 16                    # vertices per side; the sky maps are 16x16 texels
HORIZON_RADIUS = 400.0
HORIZON_V_TOP, HORIZON_V_BOTTOM = 0.01, 0.98     # `_DAT_005b27a8`, `_DAT_005b25f4`

# ATM1 "Storm": chance of a bolt per tick is dt * rate, by class 0/1/2
STORM_RATE = (0.0333, 0.0667, 0.1)
STORM_LIMITS = (0.04, 0.33, 0.66)
FLARE_POSITIONS = (1.0, 0.75, -0.75, 0.5, -0.5, 0.25, -0.25, 0.0, -1.0)   # flarepos.dat, non-zero head


def plane_to_world(d, a, b):
    """Dome-plane point (a, b) -> game XYZ, as `FUN_00461090`:
    x = (sin*b + cos*a)*scale, z = (cos*b - sin*a)*scale."""
    c, s = math.cos(d.angle * TURN), math.sin(d.angle * TURN)
    return ((s * b + c * a) * d.scale,
            (d.dome_height - (a * a + b * b) * d.dome_curvature) * d.scale,
            (c * b - s * a) * d.scale)


def sun_position(d):
    return plane_to_world(d, d.sun_x, d.sun_y)


def dome_grid(d):
    """(GRID*GRID, 3) vertices, row-major exactly like the game: row r walks
    b from -R/2 up, column c walks a from +R/2 DOWN. Vertex r*16+c takes its
    colour from sky-map texel (row r, column c)."""
    step = d.dome_radius / (GRID - 1)
    verts = np.zeros((GRID * GRID, 3), np.float32)
    for r in range(GRID):
        b = -0.5 * d.dome_radius + r * step
        for c in range(GRID):
            a = 0.5 * d.dome_radius - c * step
            verts[r * GRID + c] = plane_to_world(d, a, b)
    return verts


def dome_indices():
    idx = []
    for r in range(GRID - 1):
        for c in range(GRID - 1):
            i = r * GRID + c
            idx += [i, i + 1, i + GRID + 1, i, i + GRID + 1, i + GRID]
    return np.array(idx, np.uint32)


def dome_uv(x_scale, y_scale, offset=(0.0, 0.0)):
    """`FUN_0045ec40`: u = column / xScale + offset, v = row / yScale + offset."""
    uv = np.zeros((GRID * GRID, 2), np.float32)
    for r in range(GRID):
        for c in range(GRID):
            uv[r * GRID + c] = (c / x_scale + offset[0] if x_scale else 0.0,
                                r / y_scale + offset[1] if y_scale else 0.0)
    return uv


def wind_velocity(speed, direction_deg):
    """UV drift per game tick (`FUN_0045f1b0`): speed * 0.00025 * (sin, cos) of
    the direction, which the game negates (-1/360 turn per degree)."""
    a = -direction_deg / 360.0 * TURN
    return speed * 0.00025 * math.sin(a), speed * 0.00025 * math.cos(a)


def horizon_panels(d, columns=4):
    """16 quads of the horizon ring. Each: dict(corners=[4 x (x, y, z)] in
    game space (top-left, top-right, bottom-right, bottom-left), uv=[4 x (u, v)],
    colour_index=(i0, i1) -- columns of `hzcl` for the left and right edge).

    Slot k holds panel `slots[k] & 0xFF`, mirrored if bit 8; the panel sits at
    column `panel % columns`, row `panel // columns` of the `horz` texture,
    which has `horizon_rows` rows. `horizon_rotation` only shifts which `hzcl`
    column tints which panel."""
    rows = max(1, d.horizon_rows)
    start = (d.horizon_angle or 0.0) * TURN
    y_bot, y_top = d.horizon_bottom, d.horizon_bottom + d.horizon_top
    point = lambda k: (HORIZON_RADIUS * math.sin(start + k * TURN / 16),
                       HORIZON_RADIUS * math.cos(start + k * TURN / 16))
    out = []
    for k in range(16):
        slot = d.horizon_slots[k]
        panel, mirrored = slot & 0xFF, bool(slot & 0x100)
        col, row = panel % columns, panel // columns
        u0, u1 = col / columns, (col + 1) / columns
        if mirrored:
            u0, u1 = u1, u0
        v0, v1 = (row + HORIZON_V_TOP) / rows, (row + HORIZON_V_BOTTOM) / rows
        (xa, za), (xb, zb) = point(k), point(k + 1)
        out.append(dict(
            corners=[(xa, y_top, za), (xb, y_top, zb), (xb, y_bot, zb), (xa, y_bot, za)],
            uv=[(u0, v0), (u1, v0), (u1, v1), (u0, v1)],
            colour_index=((d.horizon_rotation + k) & 15, (d.horizon_rotation + k + 1) & 15),
            panel=panel, mirrored=mirrored))
    return out


def particle_count(value, per_unit, cap=30000):
    """Precipitation 0..1 -> number of particles (`round(value * scale)` in the
    game; the game's scale is unknown, `per_unit` is the preview knob)."""
    return int(max(0, min(cap, round(max(0.0, value) * per_unit))))


def storm_class(value):
    """-1 = no lightning, else class 0/1/2 (ATM1 thresholds)."""
    if value < STORM_LIMITS[0]:
        return -1
    if value < STORM_LIMITS[1]:
        return 0
    if value < STORM_LIMITS[2]:
        return 1
    return 2


def storm_chance(value, dt):
    """Chance of a bolt in a tick of length `dt`: dt * rate(class)."""
    c = storm_class(value)
    return 0.0 if c < 0 else min(1.0, dt * STORM_RATE[c])
