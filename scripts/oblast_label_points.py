"""Generate public/data/oblast-labels.geojson: one label anchor per oblast.

MapLibre places a symbol on every part of a MultiPolygon, which double-labels Kherson and
Chernihiv, and its anchor ignores holes, which would drop the Kyiv oblast label inside Kyiv city.
So the anchors are precomputed here: for the largest part of each oblast, the point furthest from
every ring (pole of inaccessibility), found by a coarse grid and three refinement passes.

    python scripts/oblast_label_points.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "data" / "oblasts.geojson"
OUT = ROOT / "public" / "data" / "oblast-labels.geojson"


def ring_area(ring: list[list[float]]) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))) / 2


def inside(x: float, y: float, ring: list[list[float]]) -> bool:
    on = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            on = not on
    return on


def dist_to_ring(x: float, y: float, ring: list[list[float]], kx: float) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        dx, dy = (x2 - x1) * kx, y2 - y1
        px, py = (x - x1) * kx, y - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - t * dx, py - t * dy))
    return best


def score(x: float, y: float, rings: list[list[list[float]]], kx: float) -> float:
    """Distance to the nearest ring, negative outside the polygon or inside a hole."""
    if not inside(x, y, rings[0]) or any(inside(x, y, h) for h in rings[1:]):
        return -1.0
    return min(dist_to_ring(x, y, r, kx) for r in rings)


def anchor(rings: list[list[list[float]]]) -> tuple[float, float]:
    xs = [p[0] for p in rings[0]]
    ys = [p[1] for p in rings[0]]
    kx = math.cos(math.radians(sum(ys) / len(ys)))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    best = (max(xs) / 2, max(ys) / 2, -1.0)
    for n, span in ((48, 1.0), (12, 0.12), (12, 0.03), (12, 0.008)):
        if span < 1.0:
            x0, x1 = best[0] - (max(xs) - min(xs)) * span, best[0] + (max(xs) - min(xs)) * span
            y0, y1 = best[1] - (max(ys) - min(ys)) * span, best[1] + (max(ys) - min(ys)) * span
        for i in range(n + 1):
            for j in range(n + 1):
                x = x0 + (x1 - x0) * i / n
                y = y0 + (y1 - y0) * j / n
                s = score(x, y, rings, kx)
                if s > best[2]:
                    best = (x, y, s)
    return round(best[0], 4), round(best[1], 4)


def main() -> None:
    src = json.load(open(SRC))
    feats = []
    for f in src["features"]:
        geom = f["geometry"]
        parts = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        rings = max(parts, key=lambda p: ring_area(p[0]))  # ignore islands and exclaves
        x, y = anchor(rings)
        # the map has room for "Вінницька", not "Вінницька обл." — 27 labels at country zoom collide fast
        name = f["properties"]["name"].removesuffix(" обл.")
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
                      "properties": {"oblast": f["properties"]["oblast"], "name": name}})
    body = '{"type":"FeatureCollection", "features": [\n' + ",\n".join(
        json.dumps(f, ensure_ascii=False, separators=(",", ":")) for f in feats) + "\n]}\n"
    OUT.write_text(body)
    print(f"{len(feats)} label points -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
