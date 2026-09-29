"""Vertex arrays for simple shapes, for ``Scene.create_mesh(**shape)``.

Each function returns a dict with ``positions`` (N x 3), ``normals`` (N x 3), ``uvs`` (N x 2),
all float32, and ``indices`` (M x 3, uint32). Front faces wind counterclockwise, as in glTF.
UVs follow glTF: (0, 0) is the top-left corner of an image. Units are scene units.
"""

import numpy as np

__all__ = ["box", "cylinder", "plane", "uv_sphere"]


def _mesh(positions, normals, uvs, indices):
    return {"positions": np.ascontiguousarray(positions, dtype=np.float32),
            "normals": np.ascontiguousarray(normals, dtype=np.float32),
            "uvs": np.ascontiguousarray(uvs, dtype=np.float32),
            "indices": np.ascontiguousarray(indices, dtype=np.uint32)}


def _grid(columns, rows):
    """Two triangles per cell of a (rows + 1) x (columns + 1) vertex grid, rows from the top."""
    top = np.arange(rows)[:, None] * (columns + 1) + np.arange(columns)[None, :]
    a, b = top.ravel(), top.ravel() + columns + 1
    return np.stack([np.stack([a, b, a + 1], 1), np.stack([a + 1, b, b + 1], 1)], 1).reshape(-1, 3)


def plane(width=1.0, height=1.0, *, segments=(1, 1)):
    """A rectangle in the XY plane, centered on the origin, facing +Z.

    ``segments`` is (columns, rows). More segments let ``Model.update_mesh()`` deform it.
    """
    columns, rows = (int(v) for v in segments)
    if columns < 1 or rows < 1 or not (width > 0 and height > 0):
        raise ValueError("plane needs positive width and height and at least one segment each way")
    u, v = np.meshgrid(np.linspace(0, 1, columns + 1), np.linspace(0, 1, rows + 1))
    positions = np.stack([(u - 0.5) * width, (0.5 - v) * height, np.zeros_like(u)], -1).reshape(-1, 3)
    normals = np.tile([0.0, 0.0, 1.0], (len(positions), 1))
    return _mesh(positions, normals, np.stack([u, v], -1).reshape(-1, 2), _grid(columns, rows))


def box(width=1.0, height=1.0, depth=1.0):
    """An axis-aligned box centered on the origin. Each face has its own vertices and full UVs."""
    if not (width > 0 and height > 0 and depth > 0):
        raise ValueError("box needs positive width, height, and depth")
    half = np.array([width, height, depth]) / 2
    # Outward normal, then the face's right and up directions; right x up = normal.
    faces = [((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
             ((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
             ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1))]
    positions, normals, uvs, indices = [], [], [], []
    corners = [(-1, 1, 0, 0), (1, 1, 1, 0), (-1, -1, 0, 1), (1, -1, 1, 1)]
    for face, (normal, right, up) in enumerate(faces):
        n, r, u = (np.array(v, dtype=float) for v in (normal, right, up))
        for x, y, s, t in corners:
            positions.append((n + x * r + y * u) * half)
            normals.append(n)
            uvs.append((s, t))
        base = 4 * face
        indices += [(base + 2, base + 3, base + 1), (base + 2, base + 1, base)]
    return _mesh(positions, normals, uvs, indices)


def uv_sphere(radius=0.5, *, segments=32, rings=16):
    """A sphere centered on the origin with poles on the Y axis.

    U runs once around from +Z, V from the top pole (0) to the bottom pole (1). The seam and the
    poles have duplicate vertices so the UVs stay continuous.
    """
    if segments < 3 or rings < 2 or not radius > 0:
        raise ValueError("uv_sphere needs radius > 0, segments >= 3, and rings >= 2")
    u, v = np.meshgrid(np.linspace(0, 1, segments + 1), np.linspace(0, 1, rings + 1))
    theta, phi = u * 2 * np.pi, v * np.pi
    normals = np.stack([np.sin(phi) * np.sin(theta), np.cos(phi), np.sin(phi) * np.cos(theta)], -1).reshape(-1, 3)
    triangles = _grid(segments, rings).reshape(rings, segments, 2, 3)
    # The first triangle of each top cell and the second of each bottom cell have zero area.
    keep = np.ones((rings, segments, 2), dtype=bool)
    keep[0, :, 0] = False
    keep[-1, :, 1] = False
    return _mesh(normals * radius, normals, np.stack([u, v], -1).reshape(-1, 2), triangles[keep])


def cylinder(radius=0.5, height=1.0, *, segments=32, caps=True):
    """A cylinder centered on the origin with its axis on Y.

    The side has U around from +Z and V from top (0) to bottom (1). Caps map a disc onto the
    unit UV square.
    """
    if segments < 3 or not (radius > 0 and height > 0):
        raise ValueError("cylinder needs radius > 0, height > 0, and segments >= 3")
    u, v = np.meshgrid(np.linspace(0, 1, segments + 1), [0.0, 1.0])
    theta = u * 2 * np.pi
    normals = np.stack([np.sin(theta), np.zeros_like(u), np.cos(theta)], -1).reshape(-1, 3)
    positions = normals * [radius, 0, radius] + np.stack(
        [np.zeros_like(u), (0.5 - v) * height, np.zeros_like(u)], -1).reshape(-1, 3)
    uvs = np.stack([u, v], -1).reshape(-1, 2)
    indices = _grid(segments, 1)
    if caps:
        angle = np.linspace(0, 2 * np.pi, segments + 1)[:-1]
        ring = np.stack([np.sin(angle), np.cos(angle)], -1)
        parts = [positions], [normals], [uvs], [indices]
        for sign in (1, -1):
            start = sum(len(p) for p in parts[0])
            center = [[0, sign * height / 2, 0]]
            rim = np.stack([ring[:, 0] * radius, np.full(segments, sign * height / 2), ring[:, 1] * radius], -1)
            parts[0].append(np.concatenate([center, rim]))
            parts[1].append(np.tile([0, sign, 0], (segments + 1, 1)))
            parts[2].append(np.concatenate([[[0.5, 0.5]], 0.5 + 0.5 * ring * [1, -sign]]))
            j = np.arange(segments)
            a, b = start + 1 + j, start + 1 + (j + 1) % segments
            # Viewed from outside the cap, (center, a, b) winds counterclockwise on top.
            parts[3].append(np.stack([np.full(segments, start), a, b] if sign > 0
                                     else [np.full(segments, start), b, a], 1))
        positions, normals, uvs, indices = (np.concatenate(p) for p in parts)
    return _mesh(positions, normals, uvs, indices)
