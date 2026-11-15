"""Answering agent for pipe sheets: the translator agent's pattern (Gemini function calling, tools bound to the
drawing in closures, system instruction that forbids guessing) with the pipe tools and the page router.

  python -m pipes.agent "pid_dataset/0__raw_data/sheets/test/157.jpg" "Is node_8 connected to node_22?"

Needs GEMINI_API_KEY (from .env or the environment). The model is GEMINI_MODEL, else the newest 'flash' model the
key can use. Only the question and the compact tool results are sent; no files and no coordinates.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from pipes.agent_tools import make_tools
from pipes.routing import route_page

ROOT = Path(__file__).parent.parent

SYSTEM_INSTRUCTION = """You are a construction drawing assistant for engineers, answering about ONE pipe sheet
(a P&ID or piping schematic) through deterministic tools.
Use the tools for every fact, count and connection. Do not read the drawing yourself and do not do arithmetic or
geometry yourself.
- Prefer the smallest number of tool calls. Reuse ids and evidence the tools already returned.
- Symbols from a scan are untyped (type 'unknown'): say 'symbol' and do not call one a valve, pump or tank unless
  its tag says so. Symbols from a CAD file have a type (boiler, pump, radiator, ball_valve, ...) and often a tag:
  use them, e.g. 'the boiler K1'.
- If the user names a place instead of an id, find the ids with count_symbols and a pixel region first.

RELIABILITY (every tool result has `reliable` and `caveats`):
- If `reliable` is false, say so in one short warning using the caveat, and do not present the result as exhaustive.
- Never claim that something is absent or not connected ('no such tag', '0 symbols', 'not connected') unless
  `reliable` is true. When it is false, say it was not found in the traced index and may have been missed.
- If a connection or route answer says `unknown` (pipes not analysed here), say it is unknown and why; never turn it
  into 'not connected' or 'nothing is attached'.
- A tag is read by OCR; a count by tag can be a lower bound.
- Lengths: give metres only when `length_m` is present. Otherwise say the sheet is not drawn to scale (or has no
  written length) and give the pixel length only if useful. Never convert pixels to metres.
- Pipes that inherited a tag along the run are not read directly from a label: mention it when it matters.

RESPONSE STYLE:
- Answer directly in 1-3 sentences, in the user's language. Do not expose coordinates, JSON keys or tool names.
- End with the supporting ids as 'Source: node_8, pipe_3' (only ids returned by tools; never invent an id).
"""


def _load_floor_tools():
    """The translator agent's tool functions, imported (never edited) from answering_agent/tools.py."""
    spec = importlib.util.spec_from_file_location("floor_tools", ROOT / "answering_agent" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("floor_tools", mod)
    spec.loader.exec_module(mod)
    return mod


def build_tools(route: dict, log: list) -> list:
    """Tool callables for a routed page: pipe tools and/or floor-plan tools, each bound to its own index."""
    tools: list = []
    if "pipe" in route["tool_sets"]:
        tools += make_tools(json.loads(Path(route["pipe"]).read_text(encoding="utf-8")), log)
    if "floor_plan" in route["tool_sets"]:
        ft = _load_floor_tools()
        data = json.loads(Path(route["floor_plan"][0]).read_text(encoding="utf-8"))

        def query_entities_tool(collection: str, field: str = "", value: str = "", limit: int = 50) -> dict:
            """Search active floor-plan entities (rooms, walls, openings, objects, texts) by exact field/value."""
            return ft.query_entities(data, collection, field, value, limit)

        def aggregate_tool(
            collection: str, operation: str, measure: str = "area_m2", field: str = "", value: str = ""
        ) -> dict:
            """Count or aggregate floor-plan entities: operations count, sum, min, max, average."""
            return ft.aggregate(data, collection, operation, measure, field, value)

        def check_coverage_tool(category: str, region: list[float] | None = None) -> dict:
            """Check whether a floor-plan element category was reliably analysed before claiming absence."""
            return ft.check_coverage(data, category, region)

        tools += [query_entities_tool, aggregate_tool, check_coverage_tool]
    return tools


DEFAULT_MODEL = "gemini-3.8-flash"  # the translator agent's default (answering_agent/agent.py)


def pick_model(client) -> str:
    """GEMINI_MODEL, else the translator agent's default; fall back to the newest 3.x flash the key can use."""
    if os.getenv("GEMINI_MODEL"):
        return os.environ["GEMINI_MODEL"]
    names = [m.name.removeprefix("models/") for m in client.models.list()]
    if DEFAULT_MODEL in names:
        return DEFAULT_MODEL
    flash = sorted(
        (n for n in names if re.fullmatch(r"gemini-\d+\.\d+-flash", n)),
        key=lambda n: tuple(int(x) for x in re.findall(r"\d+", n)),
    )
    if not flash:
        raise RuntimeError(f"no flash model available to this key: {names[:10]}")
    return flash[-1]


def answer_question(
    question: str, drawing_file: str, page: int = 0, *, model: str | None = None, client: Any = None
) -> dict:
    """-> {answer, route, tool_calls (full results, for highlighting), usage, model}."""
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types

    load_dotenv(ROOT / ".env")
    route = route_page(drawing_file, page)
    if not route["tool_sets"]:
        return {"answer": route["reason"], "route": route, "tool_calls": [], "usage": None, "model": None}
    if client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY in .env")
        client = genai.Client(api_key=key)
    model = model or pick_model(client)
    log: list = []
    resp = client.models.generate_content(
        model=model,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=build_tools(route, log),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=12),
            temperature=0,
        ),
    )
    u = resp.usage_metadata
    usage = (
        {
            "input": u.prompt_token_count,
            "output": u.candidates_token_count,
            "thinking": u.thoughts_token_count,
        }
        if u
        else None
    )
    return {"answer": resp.text or "", "route": route, "tool_calls": log, "usage": usage, "model": model}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    r = answer_question(argv[1], argv[0])
    print(r["answer"])
    print(
        f"\n[{r['model']}; tools: {[c['tool'] for c in r['tool_calls']]}; routed: {r['route']['tool_sets']}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
