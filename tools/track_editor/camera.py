"""Cameras for the atmosphere preview. All maths is in GL space: the CRP's
world coordinates with Z negated (the game is left-handed, GL right-handed)."""

import math

import numpy as np


def perspective(fov_y_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fov_y_deg) / 2.0)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2.0 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def look_at(eye, forward, up=(0.0, 1.0, 0.0)):
    f = np.asarray(forward, np.float64)
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.identity(4, np.float32)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ np.asarray(eye, np.float64)
    return m


def to_gl(p):
    return np.array([p[0], p[1], -p[2]], np.float64)


class FreeCamera:
    name = 'free'

    def __init__(self, position, forward):
        self.position = np.asarray(position, np.float64)
        f = np.asarray(forward, np.float64)
        self.yaw = math.atan2(f[2], f[0])
        self.pitch = math.asin(f[1] / np.linalg.norm(f))
        self.base_speed = 60.0

    def forward(self):
        return np.array([math.cos(self.pitch) * math.cos(self.yaw), math.sin(self.pitch),
                         math.cos(self.pitch) * math.sin(self.yaw)])

    def look(self, dx, dy):
        self.yaw += dx * 0.003
        self.pitch = max(-1.55, min(1.55, self.pitch - dy * 0.003))

    def move(self, dt, fwd, right, up, fast):
        f = self.forward()
        r = np.cross(f, (0.0, 1.0, 0.0))
        r /= np.linalg.norm(r)
        step = self.base_speed * (6.0 if fast else 1.0) * dt
        self.position += (f * fwd + r * right + np.array([0.0, 1.0, 0.0]) * up) * step

    def view(self):
        return look_at(self.position, self.forward())


class RailCamera:
    """Rides the centreline; the position is a float slice number."""
    name = 'centerline'

    def __init__(self, centreline, height=6.0):
        # an article covers 8 slices from its start; its Base.centre is mid-chunk
        self.slices = np.array([c[0] + 4 for c in centreline], np.float64)
        self.points = np.array([to_gl(c[1:]) for c in centreline])
        self.slice = float(self.slices[0])
        self.height = height

    def at(self, s):
        s = float(np.clip(s, self.slices[0], self.slices[-1]))
        return np.array([np.interp(s, self.slices, self.points[:, i]) for i in range(3)])

    def slice_near(self, position):
        """Fractional slice of the closest point on the centreline polyline."""
        p0, p1 = self.points[:-1], self.points[1:]
        seg = p1 - p0
        length2 = np.maximum((seg * seg).sum(axis=1), 1e-9)
        t = np.clip(((position - p0) * seg).sum(axis=1) / length2, 0.0, 1.0)
        proj = p0 + seg * t[:, None]
        i = int(np.argmin(((proj - position) ** 2).sum(axis=1)))
        return float(self.slices[i] + t[i] * (self.slices[i + 1] - self.slices[i]))

    def move(self, dt, fwd, up, fast):
        self.slice = float(np.clip(self.slice + fwd * dt * (240.0 if fast else 40.0),
                                   self.slices[0], self.slices[-1]))
        self.height = max(1.0, self.height + up * dt * 20.0)

    def position_forward(self):
        p = self.at(self.slice)
        d = self.at(self.slice + 8.0) - p
        if np.linalg.norm(d) < 1e-3:
            d = p - self.at(self.slice - 8.0)
        if np.linalg.norm(d) < 1e-3:
            d = np.array([0.0, 0.0, -1.0])
        return p + np.array([0.0, self.height, 0.0]), d

    def view(self):
        p, d = self.position_forward()
        return look_at(p, d)
