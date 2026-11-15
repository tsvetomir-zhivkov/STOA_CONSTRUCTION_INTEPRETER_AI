"""Pipe query tools for the answering agent. Read only the pipe JSON; code does all graph work and arithmetic.

  idx = PipeIndex.load("pipes/index_pipes/test_trace_det_full/157.pipe.json")
  idx.count_symbols(region=[x0, y0, x1, y1], type=None)
  idx.connected_to("node_12")
  idx.path_between("node_3", "node_9")
  idx.pipe_length(edge="pipe_4")  /  idx.pipe_length(tag="KV-1")
  python -m pipes.tools <pipe.json> <tool> [args]        # prints the JSON result

Every result has the same envelope so the UI can highlight without knowing the tool:
  answer        tool specific, small
  node_ids, edge_ids   everything the answer used (the agent must cite these)
  highlights    {"nodes": [{id, type, bbox_px, center_px}], "edges": [{id, polyline_px}], "regions": [bbox]}
                pixel coordinates of the ORIGINAL page image (frame.frame_key)
  coverage      the coverage entries that bear on the answer
  reliable      False when the index cannot back the answer (unsnapped ends, coverage not analysed, ...)
  caveats       plain-language reasons; the agent must pass them on and must not claim absence when not reliable
"""

from __future__ import annotations

import argparse
import fnmatch
import itertools
import json
import math
import sys
from collections import Counter
from pathlib import Path

import networkx as nx

from pipes.config import MEASURED_RECALL
from pipes.tags import normalise

STRUCTURAL = {"tee", "junction", "elbow", "cross", "open_end"}
MIN_SYMBOL_CONFIDENCE = 0.5  # coverage confidence below this is not "good" (CLAUDE.md: never claim absence)


def _plen(poly) -> float:
    return sum(math.dist(poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside(p, box) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


class PipeIndex:
    def __init__(self, doc: dict):
        self.doc = doc
        active = lambda el: el["provenance"].get("status", "active") == "active"  # noqa: E731
        self.nodes = {n["id"]: n for n in doc["nodes"] if active(n)}
        self.edges = {e["id"]: e for e in doc["edges"] if active(e)}
        self.symbol_ids = {i for i, n in self.nodes.items() if n["type"] not in STRUCTURAL}
        self.g = nx.MultiGraph()
        self.g.add_nodes_from(self.nodes)
        self.length_px = {i: _plen(e["polyline_px"]) for i, e in self.edges.items()}
        self.unsnapped: dict[str, list[int]] = {}  # edge id -> end indices (0/1) that reach nothing
        for i, e in self.edges.items():
            a, b = (x["node_id"] for x in e["ends"])
            for k, nid in enumerate((a, b)):
                if nid is None or nid not in self.nodes:
                    self.unsnapped.setdefault(i, []).append(k)
            if a in self.nodes and b in self.nodes and a != b:
                self.g.add_edge(a, b, key=i, length=self.length_px[i])

    @classmethod
    def load(cls, path: str | Path) -> PipeIndex:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ shared pieces

    def _node_hl(self, ids) -> list[dict]:
        return [
            {"id": i, "type": self.nodes[i]["type"], "tag": self.nodes[i].get("tag"),
             "bbox_px": self.nodes[i]["bbox_px"], "center_px": self.nodes[i]["center_px"]}
            for i in ids if i in self.nodes
        ]  # fmt: skip

    def _edge_hl(self, ids) -> list[dict]:
        return [
            {"id": i, "line_tag": self.edges[i].get("line_tag"), "polyline_px": self.edges[i]["polyline_px"]}
            for i in ids
            if i in self.edges
        ]

    def _unattached_tag_texts(self, pattern: str) -> list[dict]:
        """Texts on the sheet that read like `pattern` but are attached to no symbol and no pipe."""
        pat = normalise(pattern)
        return [
            {"text_id": t["id"], "raw_text": t["raw_text"], "bbox_px": t["bbox_px"]}
            for t in self.doc["texts"]
            if not t["attached_to"] and fnmatch.fnmatchcase(normalise(t["raw_text"]), pat)
        ]

    def _oriented(self, edge_id: str, start_node: str) -> list:
        """Edge polyline running away from start_node."""
        e = self.edges[edge_id]
        poly = e["polyline_px"]
        return poly if e["ends"][0]["node_id"] == start_node else poly[::-1]

    def _coverage(self, category: str, region=None) -> list[dict]:
        out = []
        for c in self.doc["coverage"]:
            if c["category"] != category:
                continue
            if region is None or c["region"] == "page" or _overlap(c["region"], region):
                out.append(
                    {k: c[k] for k in ("id", "category", "status", "confidence", "region", "note")}
                    | {"element_ids": c.get("element_ids", [])}
                )
        return out

    def _pipe_gaps(self, node_ids) -> list[dict]:
        """Pipes/connectivity coverage entries that are not_analysed or partial over the page or over these nodes."""
        boxes = [self.nodes[n]["bbox_px"] for n in node_ids if n in self.nodes]
        out = []
        for cat in ("pipes", "connectivity"):
            for c in self._coverage(cat):
                if c["status"] == "analysed":
                    continue
                if c["region"] == "page" or any(_overlap(c["region"], b) for b in boxes):
                    out.append(c)
        return out

    def _unknown(self, tool: str, answer: dict, node_ids, gaps: list[dict]) -> dict:
        """The honest answer when the traced graph says nothing about these nodes: unknown, not 'not connected'."""
        why = "; ".join(
            f"{c['category']} {c['status']}"
            + (f" in {c['region']}" if c["region"] != "page" else " on the whole page")
            + (f" ({c['note']})" if c.get("note") else "")
            for c in gaps[:3]
        )
        answer = {**answer, "connected": None, "unknown": True, "reason": "unknown: pipes not analysed here"}
        return self._result(
            tool, answer, list(node_ids), [], gaps, False,
            [f"unknown, pipes not analysed here: {why}. The absence of a route or of neighbours proves nothing."],
            [c["region"] for c in gaps if c["region"] != "page"],
        )  # fmt: skip

    def _result(self, tool, answer, node_ids, edge_ids, coverage, reliable, caveats, regions=None) -> dict:
        node_ids, edge_ids = list(dict.fromkeys(node_ids)), list(dict.fromkeys(edge_ids))
        return {
            "tool": tool,
            "sheet": self.doc["sheet"]["id"],
            "answer": answer,
            "node_ids": node_ids,
            "edge_ids": edge_ids,
            "highlights": {
                "nodes": self._node_hl(node_ids),
                "edges": self._edge_hl(edge_ids),
                "regions": regions or [],
            },
            "coverage": coverage,
            "reliable": bool(reliable),
            "caveats": caveats,
        }

    def _error(self, tool: str, msg: str) -> dict:
        return self._result(tool, {"error": msg}, [], [], [], False, [msg])

    def _component_unsnapped(self, node_ids) -> list[str]:
        """Edges with an unsnapped end whose other end lies in the same component as one of node_ids."""
        comp = set()
        for n in node_ids:
            if n in self.g:
                comp |= nx.node_connected_component(self.g, n)
        out = []
        for eid, _ in self.unsnapped.items():
            ends = [x["node_id"] for x in self.edges[eid]["ends"]]
            if any(x in comp for x in ends if x):
                out.append(eid)
        return out

    def _tag_check(self, pattern: str, n_found: int, target: str) -> tuple[list[dict], bool, list[str]]:
        """(tags coverage entries, reliable?, caveats) for a tag query.

        No tags coverage, or text that reads like the tag but is attached to nothing, makes the answer unreliable.
        Partly attached tags elsewhere on the sheet only block an absence claim (a result of 0).
        """
        cov = self._coverage("tags")
        caveats = []
        ok = True
        for u in self._unattached_tag_texts(pattern):
            ok = False
            caveats.append(
                f"the text {u['raw_text']!r} at {u['bbox_px']} matches the tag but is attached to no {target}"
            )
        if not cov:
            return (
                cov,
                False,
                ["tags were not analysed on this sheet: absence of a tag proves nothing", *caveats],
            )
        partial = [c for c in cov if c["status"] != "analysed"]
        if partial:
            n = sum(len(c["element_ids"]) for c in partial)
            caveats.append(
                f"tags are only partly attached on this sheet: {n} tag-like text(s) found no symbol or pipe"
            )
            if n_found == 0:
                ok = False
                caveats.append("nothing matched and tags are partial: do not say the tag is absent")
        if self.doc["sheet"]["route"] == "vector_parser":
            caveats.append(
                "tags come from the drawing file's own text and block attributes, matched by proximity"
            )
        else:
            caveats.append(
                "tags are read by OCR: a misread or missed tag is not matched, so a result can be a lower bound"
            )
        return cov, ok, caveats

    # ------------------------------------------------------------------ tools

    def count_symbols(self, region=None, type: str | None = None, tag: str | None = None) -> dict:  # noqa: A002
        """Count reported symbols, optionally in a bbox [x0,y0,x1,y1], of one type and/or with a tag ('V-601', 'V-*')."""
        if region is not None and (len(region) != 4 or region[0] >= region[2] or region[1] >= region[3]):
            return self._error(
                "count_symbols", f"region must be [x0, y0, x1, y1] with x0<x1, y0<y1, got {region}"
            )
        ids = [
            i for i in sorted(self.symbol_ids, key=lambda s: int(s.split("_")[-1]))
            if (region is None or _inside(self.nodes[i]["center_px"], region))
            and (type is None or self.nodes[i]["type"] == type)
            and (tag is None or fnmatch.fnmatchcase(normalise(self.nodes[i].get("tag") or ""), normalise(tag)))
        ]  # fmt: skip
        cov = self._coverage("symbols", region)
        caveats = []
        reliable = bool(cov) and all(
            c["status"] == "analysed" and c["confidence"] >= MIN_SYMBOL_CONFIDENCE for c in cov
        )
        if not cov:
            caveats.append("no symbol coverage entry for this region: the index cannot say what was analysed")
        for c in cov:
            if c["status"] != "analysed":
                caveats.append(f"symbols are {c['status']} in {c['region']} ({c['id']})")
            elif c["confidence"] < MIN_SYMBOL_CONFIDENCE:
                caveats.append(f"symbol detection confidence is only {c['confidence']:.2f} ({c['id']})")
        if tag is not None:
            tcov, ok, tcav = self._tag_check(tag, len(ids), "symbol")
            cov, caveats = cov + tcov, caveats + tcav
            reliable = reliable and ok
        if not ids and not reliable:
            caveats.append(
                "0 symbols found but the region was not reliably analysed: do not say there are none"
            )
        equipment = [i for i in ids if self.nodes[i].get("subtype") == "equipment"]
        if equipment:
            caveats.append(
                f"{len(equipment)} of the counted nodes are equipment outlines found by shape, not by the detector"
            )
        types = dict(Counter(self.nodes[i]["type"] for i in ids))
        if set(types) == {"unknown"}:
            caveats.append("the detector is single-class: symbols are counted but not typed")
        if any(
            c.get("extractor_id") == "ext_det" for c in self.doc["coverage"] if c["category"] == "symbols"
        ):
            caveats.append(
                f"symbols come from a detector that found about {MEASURED_RECALL:.0%} of the annotated symbols on "
                "the test sheets: a count can be too low (it is not exhaustive)"
            )
        return self._result(
            "count_symbols",
            {"count": len(ids), "by_type": types, "type_filter": type, "tag_filter": tag,
             "tags": {i: self.nodes[i]["tag"] for i in ids if self.nodes[i].get("tag")}},
            ids, [], cov, reliable, caveats, [list(region)] if region else [],
        )  # fmt: skip

    def connected_to(self, node: str) -> dict:
        """Symbols directly connected to `node`: reached through pipes and tees/junctions, not through another symbol."""
        if node not in self.nodes:
            return self._error("connected_to", f"unknown node id {node!r}")
        neighbours: dict[str, dict] = {}
        used_edges: list[str] = []
        used_nodes = [node]
        seen = {node}
        stack = [(node, [], [])]  # (current, edge ids so far, polylines so far)
        open_ends = []
        while stack:
            cur, eids, polys = stack.pop()
            for _, nb, key in self.g.edges(cur, keys=True):
                if key in eids:
                    continue
                path_e, path_p = [*eids, key], [*polys, self._oriented(key, cur)]
                if nb in self.symbol_ids and nb != node:
                    if nb not in neighbours or len(path_e) < len(neighbours[nb]["edge_ids"]):
                        neighbours[nb] = {
                            "node_id": nb,
                            "type": self.nodes[nb]["type"],
                            "tag": self.nodes[nb].get("tag"),
                            "edge_ids": path_e,
                            "polylines_px": path_p,
                        }
                elif nb not in seen and nb not in self.symbol_ids:
                    seen.add(nb)
                    used_nodes.append(nb)
                    if self.nodes[nb]["type"] == "open_end":
                        open_ends.append(nb)
                    stack.append((nb, path_e, path_p))
        for n in neighbours.values():
            used_edges += n["edge_ids"]
            used_nodes.append(n["node_id"])
        # edges around the start node and the structural nodes explored: any dangling end means unknown links
        explored = set(used_nodes)
        dangling = [
            eid for eid in self.unsnapped
            if any(x["node_id"] in explored for x in self.edges[eid]["ends"] if x["node_id"])
        ]  # fmt: skip
        caveats = []
        if dangling:
            caveats.append(
                f"{len(dangling)} pipe end(s) next to this node's pipes reach nothing (unsnapped): "
                "there may be further connections the tracer did not close"
            )
        if open_ends:
            caveats.append(f"pipes leave the sheet or end open at {open_ends}")
        if not neighbours and (gaps := self._pipe_gaps([node])):
            return self._unknown(
                "connected_to",
                {
                    "node_id": node,
                    "n_connected": None,
                    "links": [],
                    "open_ends": open_ends,
                    "dangling_edge_ids": dangling,
                },
                [node],
                gaps,
            )
        if self.nodes[node]["type"] not in STRUCTURAL and not neighbours and not dangling:
            caveats.append("no pipe reaches this symbol in the traced graph")
        return self._result(
            "connected_to",
            {"node_id": node, "connected": sorted(neighbours), "n_connected": len(neighbours),
             "links": list(neighbours.values()), "open_ends": open_ends, "dangling_edge_ids": dangling},
            [*used_nodes], [*used_edges, *dangling], self._coverage("connectivity"), not dangling, caveats,
        )  # fmt: skip

    def path_between(self, a: str, b: str) -> dict:
        """Shortest pipe route between two nodes (it may run through in-line symbols); the evidence for 'is A connected to B'."""
        for n in (a, b):
            if n not in self.nodes:
                return self._error("path_between", f"unknown node id {n!r}")
        if a == b:
            return self._error("path_between", "a and b are the same node")
        try:
            path = nx.shortest_path(self.g, a, b, weight=lambda u, v, d: min(x["length"] for x in d.values()))
        except nx.NetworkXNoPath:
            path = None
        cov = self._coverage("connectivity")
        if path is None and (gaps := self._pipe_gaps([a, b])):
            return self._unknown("path_between", {"a": a, "b": b, "dangling_edge_ids": []}, [a, b], gaps)
        if path is None:
            dangling = self._component_unsnapped([a, b])
            caveats = ["no pipe route between them in the traced graph"]
            if dangling:
                caveats.append(
                    f"{len(dangling)} unsnapped pipe end(s) in the same pipe runs: the tracer may have missed "
                    "the link, so this is 'not connected as traced', not 'not connected'"
                )
            return self._result(
                "path_between", {"connected": False, "a": a, "b": b, "dangling_edge_ids": dangling},
                [a, b], dangling, cov, not dangling, caveats,
            )  # fmt: skip
        edge_ids, poly = [], []
        for u, v in itertools.pairwise(path):
            key = min(self.g[u][v], key=lambda k: self.g[u][v][k]["length"])
            edge_ids.append(key)
            seg = self._oriented(key, u)
            poly += seg if not poly else seg[1:]
        via = [n for n in path[1:-1] if n in self.symbol_ids]
        total = sum(self.length_px[e] for e in edge_ids)
        caveats = []
        if via:
            caveats.append(f"the route runs through in-line symbol(s) {via}")
        return self._result(
            "path_between",
            {"connected": True, "a": a, "b": b, "path_node_ids": path, "path_edge_ids": edge_ids,
             "via_symbols": via, "length_px": round(total, 1), "path_polyline_px": poly},
            path, edge_ids, cov, True, caveats,
        )  # fmt: skip

    def pipe_length(self, edge: str | None = None, tag: str | None = None) -> dict:
        """Length of one pipe edge, or of every edge carrying a line tag. Lower bound when lengths are missing."""
        if (edge is None) == (tag is None):
            return self._error("pipe_length", "give exactly one of edge or tag")
        if edge is not None:
            if edge not in self.edges:
                return self._error("pipe_length", f"unknown edge id {edge!r}")
            ids = [edge]
        else:
            pat = normalise(tag)
            ids = [
                i
                for i, e in self.edges.items()
                if fnmatch.fnmatchcase(normalise(e.get("line_tag") or ""), pat)
            ]
        caveats, cov = [], []
        reliable = True
        if tag is not None:
            cov, reliable, caveats = self._tag_check(tag, len(ids), "pipe")
            if not ids:
                caveats.append(f"no edge carries the line tag {tag!r}")
        lengths = {}
        for i in ids:
            ln = self.edges[i]["length"]
            lengths[i] = ln["written_m"] if ln["written_m"] is not None else ln["geometry_m"]
        known = {i: v for i, v in lengths.items() if v is not None}
        missing = [i for i in ids if i not in known]
        basis = "none"
        if known:
            kinds = {
                "written" if self.edges[i]["length"]["written_m"] is not None else "geometry" for i in known
            }
            basis = kinds.pop() if len(kinds) == 1 else "mixed"
        total_px = round(sum(self.length_px[i] for i in ids), 1)
        carried = [
            i
            for i in ids
            if any(x.startswith("pipe_") for x in self.edges[i]["provenance"].get("evidence_ids", []))
        ]
        if carried:
            caveats.append(
                f"{len(carried)} of these pipe(s) inherit the tag along the run through in-line symbols, "
                "not from a label next to them"
            )
        if not known and ids:
            scale = self.doc["scale"]
            caveats.append(
                "no length in metres: "
                + (
                    "the sheet is not drawn to scale and has no written length"
                    if scale["source"] == "not_to_scale"
                    else "no scale was recovered"
                )
                + "; only the pixel length is given"
            )
        elif missing:
            caveats.append(f"{len(missing)} edge(s) have no length in metres: the total is a lower bound")
        for i in ids:
            if i in self.unsnapped:
                caveats.append(f"{i} has an unsnapped end: it may continue past where it was traced")
                break
        return self._result(
            "pipe_length",
            {"edge": edge, "tag": tag, "n_edges": len(ids), "length_m": round(sum(known.values()), 3) if known else None,
             "length_px": total_px, "basis": basis, "lower_bound": bool(missing and known),
             "missing_length_edge_ids": missing, "per_edge_m": lengths, "propagated_edge_ids": carried,
             "tags_found": sorted({self.edges[i]["line_tag"] for i in ids})},
            [self.edges[i]["ends"][k]["node_id"] for i in ids for k in (0, 1) if self.edges[i]["ends"][k]["node_id"]],
            ids, cov, reliable, caveats,
        )  # fmt: skip


# ---------------------------------------------------------------- CLI


def add_tool_args(sub) -> None:
    """The tool sub-commands, shared with pipes.demo."""
    c = sub.add_parser("count_symbols")
    c.add_argument("--region", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    c.add_argument("--type")
    c.add_argument("--tag")
    sub.add_parser("connected_to").add_argument("node")
    p = sub.add_parser("path_between")
    p.add_argument("a")
    p.add_argument("b")
    ln = sub.add_parser("pipe_length")
    ln.add_argument("--edge")
    ln.add_argument("--tag")


def call(idx: PipeIndex, a: argparse.Namespace) -> dict:
    if a.tool == "count_symbols":
        return idx.count_symbols(a.region, a.type, a.tag)
    if a.tool == "connected_to":
        return idx.connected_to(a.node)
    if a.tool == "path_between":
        return idx.path_between(a.a, a.b)
    return idx.pipe_length(a.edge, a.tag)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m pipes.tools", description=__doc__)
    ap.add_argument("pipe_json")
    add_tool_args(ap.add_subparsers(dest="tool", required=True))
    a = ap.parse_args(argv)
    print(json.dumps(call(PipeIndex.load(a.pipe_json), a), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
