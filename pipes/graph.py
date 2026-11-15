"""Graph builder (plan step 8.2): traced segments + symbol boxes -> nodes and edges. Image independent.

Rules (the ones that make answers trustworthy):
  * a 4-way skeleton point is two straight-through lines that are NOT connected, unless a junction dot is there;
  * a 3-way point is a tee node;
  * an end that reaches nothing is `unsnapped` and gets a connectivity coverage entry (done by the caller).
All distances are multiples of the stroke width w. Nothing is deleted silently: tiny fragments are counted.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

CONTRACT_W = 2.0  # branch points closer than this many w are one point
SPUR_W = 2.0  # dangling edges shorter than this many w are spurs
STITCH_W = 6.0  # collinear end gap bridged without text evidence
STITCH_TEXT_MAX = 300.0  # px: a gap this short is bridged when it lies inside erased text
STITCH_ANGLE = 8.0  # degrees
CROSS_COS = -1.7  # sum of the two pair cosines for a straight-through crossing (perfect = -2)
FRAGMENT_W = 3.0  # edges shorter than this many w with no snapped end are noise


def plen(poly) -> float:
    return sum(math.dist(poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def _unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else (0.0, 0.0)


def rect_dist(p, box) -> float:
    dx = max(box[0] - p[0], 0, p[0] - box[2])
    dy = max(box[1] - p[1], 0, p[1] - box[3])
    return math.hypot(dx, dy)


@dataclass
class G:
    nodes: dict[int, dict] = field(default_factory=dict)  # id -> {pos, kind: plain|tee|junction}
    edges: list[dict] = field(default_factory=list)  # {a, b, poly}
    next_id: int = 0

    def add_node(self, pos, kind="plain") -> int:
        self.next_id += 1
        self.nodes[self.next_id] = {"pos": list(pos), "kind": kind}
        return self.next_id

    def inc(self) -> dict[int, list[tuple[int, bool]]]:
        """node -> [(edge index, node is the 'a' end)]"""
        d: dict[int, list[tuple[int, bool]]] = {n: [] for n in self.nodes}
        for i, e in enumerate(self.edges):
            d[e["a"]].append((i, True))
            d[e["b"]].append((i, False))
        return d

    def direction(self, ei: int, at_a: bool, w: float) -> tuple[float, float]:
        """Unit vector leaving the node along the edge, measured over ~3w."""
        poly = self.edges[ei]["poly"] if at_a else self.edges[ei]["poly"][::-1]
        p0, acc = poly[0], 0.0
        for q in poly[1:]:
            acc += math.dist(p0, q)
            p0 = q
            if acc >= 3 * w:
                break
        return _unit((p0[0] - poly[0][0], p0[1] - poly[0][1]))


def from_trace(nodes: dict[int, list[float]], edges: list[dict]) -> G:
    g = G()
    m = {}
    for n, pos in nodes.items():
        m[n] = g.add_node(pos)
    g.edges = [{"a": m[e["a"]], "b": m[e["b"]], "poly": e["poly"]} for e in edges]
    return g


def collapse_deg2(g: G) -> None:
    """Merge the two edges at every plain degree-2 node into one polyline."""
    changed = True
    while changed:
        changed = False
        inc = g.inc()
        for n, lst in inc.items():
            if g.nodes[n]["kind"] != "plain" or len(lst) != 2:
                continue
            (i, ia), (j, ja) = lst
            if i == j:
                continue
            e1, e2 = g.edges[i], g.edges[j]
            p1 = e1["poly"][::-1] if ia else e1["poly"]  # ends at n
            p2 = e2["poly"] if ja else e2["poly"][::-1]  # starts at n
            a = e1["b"] if ia else e1["a"]
            b = e2["b"] if ja else e2["a"]
            la, lb = e1.get("layer"), e2.get("layer")
            layer = la if la == lb else "+".join(sorted({x for x in (la, lb) if x})) or None
            merged = {
                "a": a,
                "b": b,
                "poly": p1 + p2[1:],
                "layer": layer,
                "src": [*e1.get("src", []), *e2.get("src", [])],
            }
            g.edges = [e for k, e in enumerate(g.edges) if k not in (i, j)] + [merged]
            del g.nodes[n]
            changed = True
            break


def prune_spurs(g: G, w: float) -> int:
    n = 0
    inc = g.inc()
    drop = set()
    for i, e in enumerate(g.edges):
        if plen(e["poly"]) >= SPUR_W * w:
            continue
        da, db = len(inc[e["a"]]), len(inc[e["b"]])
        if (da == 1 and db >= 3) or (db == 1 and da >= 3):
            drop.add(i)
    for i in sorted(drop, reverse=True):
        e = g.edges.pop(i)
        for k in (e["a"], e["b"]):
            if not any(k in (x["a"], x["b"]) for x in g.edges):
                g.nodes.pop(k, None)
        n += 1
    collapse_deg2(g)
    return n


def contract_close(g: G, w: float) -> None:
    """Branch points (degree >= 3) within CONTRACT_W*w of each other become one node (X crossings often
    come out of the skeleton as two close tees)."""
    inc = g.inc()
    br = [n for n, lst in inc.items() if len(lst) >= 3]
    parent = {n: n for n in br}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in g.edges:
        a, b = e["a"], e["b"]
        if a in parent and b in parent and a != b and plen(e["poly"]) <= CONTRACT_W * w:
            parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for n in br:
        groups.setdefault(find(n), []).append(n)
    for root, members in groups.items():
        if len(members) < 2:
            continue
        pos = np.mean([g.nodes[m]["pos"] for m in members], axis=0).tolist()
        g.nodes[root]["pos"] = pos
        for m in members:
            if m != root:
                del g.nodes[m]
        keep = []
        for e in g.edges:
            a = root if e["a"] in members else e["a"]
            b = root if e["b"] in members else e["b"]
            if a == b == root and plen(e["poly"]) <= CONTRACT_W * w:
                continue  # the short edge inside the group
            poly = list(e["poly"])
            if a == root:
                poly[0] = list(pos)
            if b == root:
                poly[-1] = list(pos)
            keep.append({"a": a, "b": b, "poly": poly})
        g.edges = keep


def resolve_branches(g: G, w: float, has_dot: Callable[[list[float]], bool]) -> dict:
    """degree 3 -> tee; degree 4 -> crossing (two unconnected runs) unless a junction dot; >= 5 -> junction."""
    stats = {"tee": 0, "junction": 0, "crossing": 0}
    for n, lst in list(g.inc().items()):
        k = len(lst)
        if k < 3:
            continue
        pos = g.nodes[n]["pos"]
        if k == 3:
            g.nodes[n]["kind"] = "tee"
            stats["tee"] += 1
            continue
        if k == 4 and not has_dot(pos):
            dirs = [g.direction(i, at_a, w) for i, at_a in lst]
            best = None
            for pairing in (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2))):
                s = sum(dirs[p][0] * dirs[q][0] + dirs[p][1] * dirs[q][1] for p, q in pairing)
                if best is None or s < best[0]:
                    best = (s, pairing)
            if best[0] <= CROSS_COS:
                for p, q in best[1]:  # one new plain node per straight-through run
                    nn = g.add_node(pos)
                    for idx in (p, q):
                        ei, at_a = lst[idx]
                        g.edges[ei]["a" if at_a else "b"] = nn
                del g.nodes[n]
                stats["crossing"] += 1
                continue
        g.nodes[n]["kind"] = "junction"
        stats["junction"] += 1
    collapse_deg2(g)
    return stats


def stitch(g: G, w: float, text: np.ndarray | None) -> int:
    """Join two free ends that point at each other: gap < STITCH_W*w, or inside erased text (cut pipes)."""
    n_join = 0
    while True:
        inc = g.inc()
        ends = [(n, lst[0]) for n, lst in inc.items() if len(lst) == 1 and g.nodes[n]["kind"] == "plain"]
        best = None
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                (na, (ea, aa)), (nb, (eb, ab)) = ends[i], ends[j]
                if ea == eb:
                    continue
                pa, pb = g.nodes[na]["pos"], g.nodes[nb]["pos"]
                gap = math.dist(pa, pb)
                if gap < 1e-6 or gap > STITCH_TEXT_MAX:
                    continue
                u = _unit((pb[0] - pa[0], pb[1] - pa[1]))
                da, db = g.direction(ea, aa, w), g.direction(eb, ab, w)
                ang_a = math.degrees(math.acos(max(-1, min(1, -(da[0] * u[0] + da[1] * u[1])))))
                ang_b = math.degrees(math.acos(max(-1, min(1, db[0] * u[0] + db[1] * u[1]))))
                if ang_a > STITCH_ANGLE or ang_b > STITCH_ANGLE:
                    continue
                ok = gap < STITCH_W * w
                if not ok and text is not None:
                    ts = np.linspace(0, 1, max(8, int(gap)))
                    xs = np.clip(np.round(pa[0] + ts * (pb[0] - pa[0])).astype(int), 0, text.shape[1] - 1)
                    ys = np.clip(np.round(pa[1] + ts * (pb[1] - pa[1])).astype(int), 0, text.shape[0] - 1)
                    ok = bool(text[ys, xs].mean() >= 0.8)
                if ok and (best is None or gap < best[0]):
                    best = (gap, na, nb)
        if best is None:
            return n_join
        _, na, nb = best
        for e in g.edges:  # merge nb into na
            for k in ("a", "b"):
                if e[k] == nb:
                    e[k] = na
        # the joined edge's bridge: extend the polyline of the edge that used to end at nb
        pa = g.nodes[na]["pos"]
        for e in g.edges:
            if e["a"] == na and math.dist(e["poly"][0], pa) > 1e-6:
                e["poly"] = [list(pa), *e["poly"]]
            if e["b"] == na and math.dist(e["poly"][-1], pa) > 1e-6:
                e["poly"] = [*e["poly"], list(pa)]
        del g.nodes[nb]
        n_join += 1
        collapse_deg2(g)


def edge_doc(
    i: int,
    ends: list[dict],
    polyline: list,
    extractor: str,
    conf: float,
    line_style: str = "solid",
) -> dict:
    """One schema edge. ends[].point_px must equal the first/last polyline point (validator rule)."""
    prov = {"extractor_id": extractor, "confidence": round(float(conf), 4), "source_ref": None}
    ends = [{**ends[0], "point_px": polyline[0]}, {**ends[1], "point_px": polyline[-1]}]
    return {
        "id": f"pipe_{i}",
        "ends": ends,
        "polyline_px": polyline,
        "line_tag": None,
        "line_tag_text_id": None,
        "system": "unknown",
        "system_raw": None,
        "diameter": {"value": None, "source": "unknown", "text_id": None, "confidence": 0.0},
        "material": None,
        "insulation": None,
        "line_style": line_style,
        "layer": None,
        "flow": {"direction": "unknown", "evidence_ids": [], "confidence": 0.0},
        "length": {
            "geometry_m": None,
            "written_m": None,
            "written_text_id": None,
            "difference_pct": None,
            "flag": "not_to_scale",
        },
        "elevation_m": None,
        "provenance": prov,
    }
