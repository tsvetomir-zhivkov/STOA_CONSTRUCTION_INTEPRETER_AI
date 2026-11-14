"""The four pipe tools as an agent exposes them: JSON definitions, bound callables, a generic dispatcher.

The translator's answering agent (answering_agent/agent.py) registers plain Python callables whose docstrings and type
hints become the tool schema, named `<tool>_tool`, with the drawing bound privately in a closure so the model never
supplies it. `make_tools` does exactly that for a pipe index. `TOOL_DEFINITIONS` is the same four tools as JSON
(name, description, parameters as JSON Schema) for any agent that registers tools as JSON; `call_tool` validates a
JSON call against it and runs it.

The model sees a COMPACT result (ids, counts, flags, caveats; no polylines or boxes). The FULL result, with the
pixel geometry the UI draws, goes to `log` so a viewer can highlight it afterwards (pipes.demo.render).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pipes.tools import PipeIndex

_REGION = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 4,
    "maxItems": 4,
    "description": "Optional pixel box [left, top, right, bottom] of the original page image; omit for the whole sheet.",
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "count_symbols",
        "description": (
            "Count the symbols (valves, instruments, equipment) the index found on this pipe sheet, optionally in a "
            "pixel region, of one type, or carrying a tag such as 'V-601' or a pattern such as 'V-*'. Returns the "
            "count, the symbol ids and their tags. Symbols are single-class (untyped). A count of 0 is only a real "
            "answer when 'reliable' is true. Use for 'how many ...' and 'which symbol is tagged ...'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "region": _REGION,
                "type": {
                    "type": "string",
                    "description": "Optional symbol type, e.g. 'unknown' (the detector is untyped).",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional tag or pattern with * wildcards, e.g. 'P-*'. Case-insensitive.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "connected_to",
        "description": (
            "List the symbols directly connected to one node (a symbol id such as 'node_12') through pipes, tees and "
            "junctions, without passing through another symbol. Returns the neighbour ids and the pipe ids. Warns "
            "when pipe ends near the node reach nothing (unsnapped), meaning more connections may exist."
        ),
        "parameters": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Node id, e.g. 'node_12'."}},
            "required": ["node"],
        },
    },
    {
        "name": "path_between",
        "description": (
            "Find the shortest pipe route between two nodes ('is A connected to B', 'what is between A and B'). "
            "Returns connected true/false, the node and pipe ids on the route, the in-line symbols it passes and "
            "its length in pixels. 'Not connected' is only certain when 'reliable' is true; unsnapped pipe ends make "
            "it 'not connected as traced'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "First node id."},
                "b": {"type": "string", "description": "Second node id."},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "pipe_length",
        "description": (
            "Length of one pipe (edge id such as 'pipe_4') or of every pipe carrying a line number/tag such as "
            "'4\"-S1-P-10014'. Gives metres only when the sheet is to scale or the length is written on it; "
            "otherwise only pixels, and says so. Flags a lower bound when some pipes have no length, and pipes that "
            "inherited the tag along the run. Give exactly one of edge or tag."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "edge": {"type": "string", "description": "Pipe id, e.g. 'pipe_4'."},
                "tag": {"type": "string", "description": "Line number or tag; * wildcards allowed."},
            },
            "required": [],
        },
    },
]

_BY_NAME = {d["name"]: d for d in TOOL_DEFINITIONS}
_PY_TYPES = {"string": str, "array": list}


def compact(result: dict, max_ids: int = 40) -> dict:
    """What the model gets: no polylines, no boxes, coverage reduced to status and confidence."""
    r = {k: result[k] for k in ("tool", "sheet", "reliable", "caveats") if k in result}
    ans = {k: v for k, v in result["answer"].items() if k not in ("path_polyline_px", "per_edge_m", "links")}
    if "links" in result["answer"]:
        ans["links"] = [
            {"node_id": x["node_id"], "edge_ids": x["edge_ids"]} for x in result["answer"]["links"]
        ]
    r["answer"] = ans
    for key in ("node_ids", "edge_ids"):
        ids = result.get(key, [])
        r[key] = ids[:max_ids]
        if len(ids) > max_ids:
            r[f"{key}_truncated"] = len(ids)
    r["coverage"] = [
        {k: c[k] for k in ("category", "status", "confidence")} for c in result.get("coverage", [])
    ]
    return r


def validate_args(name: str, args: dict) -> str | None:
    """None when the JSON call is valid for TOOL_DEFINITIONS, else a message."""
    d = _BY_NAME.get(name)
    if d is None:
        return f"unknown tool {name!r}; tools are {sorted(_BY_NAME)}"
    props = d["parameters"]["properties"]
    for k in args:
        if k not in props:
            return f"{name}: unknown argument {k!r}; allowed: {sorted(props)}"
    for k in d["parameters"]["required"]:
        if k not in args:
            return f"{name}: missing required argument {k!r}"
    for k, v in args.items():
        want = props[k]["type"]
        if not isinstance(v, _PY_TYPES[want]):
            return f"{name}: argument {k!r} must be a {want}"
        if want == "array" and (
            len(v) != 4 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
        ):
            return f"{name}: {k!r} must be four numbers [left, top, right, bottom]"
    return None


def call_tool(index: PipeIndex, name: str, args: dict | None = None) -> dict:
    """Run one JSON tool call; returns the FULL result, or an error result for a bad call (never raises)."""
    args = {
        k: v for k, v in dict(args or {}).items() if v not in ("", None)
    }  # empty or null means "not given"
    err = validate_args(name, args)
    if err:
        return index._error(name if name in _BY_NAME else "pipe_tools", err)
    if name == "count_symbols":
        return index.count_symbols(args.get("region"), args.get("type"), args.get("tag"))
    if name == "connected_to":
        return index.connected_to(args["node"])
    if name == "path_between":
        return index.path_between(args["a"], args["b"])
    return index.pipe_length(args.get("edge"), args.get("tag"))


def make_tools(doc_or_index: dict | PipeIndex, log: list | None = None) -> list[Callable]:
    """Callables for an agent's `tools=[...]`, the pipe index bound privately. Full results are appended to `log`."""
    index = doc_or_index if isinstance(doc_or_index, PipeIndex) else PipeIndex(doc_or_index)

    def run(name: str, **kw) -> dict:
        full = call_tool(index, name, kw)
        if log is not None:
            log.append(
                {"tool": name, "args": {k: v for k, v in kw.items() if v not in ("", None)}, "result": full}
            )
        return compact(full)

    def count_symbols_tool(region: list[float] | None = None, type: str = "", tag: str = "") -> dict:  # noqa: A002
        return run("count_symbols", region=region, type=type, tag=tag)

    def connected_to_tool(node: str) -> dict:
        return run("connected_to", node=node)

    def path_between_tool(a: str, b: str) -> dict:
        return run("path_between", a=a, b=b)

    def pipe_length_tool(edge: str = "", tag: str = "") -> dict:
        return run("pipe_length", edge=edge, tag=tag)

    count_symbols_tool.__doc__ = _BY_NAME["count_symbols"]["description"] + (
        " Arguments: region = optional pixel box [left, top, right, bottom]; type = optional symbol type; "
        "tag = optional tag or pattern with * wildcards."
    )
    connected_to_tool.__doc__ = _BY_NAME["connected_to"]["description"] + " Argument: node = node id."
    path_between_tool.__doc__ = _BY_NAME["path_between"]["description"] + " Arguments: a, b = node ids."
    pipe_length_tool.__doc__ = (
        _BY_NAME["pipe_length"]["description"] + " Arguments: edge = pipe id; tag = line number or tag."
    )
    return [count_symbols_tool, connected_to_tool, path_between_tool, pipe_length_tool]
