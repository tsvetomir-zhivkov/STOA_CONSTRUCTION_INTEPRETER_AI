"""Ten example questions on dev sheets 157, 151 and 176, with expected answers, run through the pipe agent.

  python -m pipes.eval.agent_questions                # agent (Gemini) + direct tool check
  python -m pipes.eval.agent_questions --tools-only   # only call the reference tool of each question, no LLM

Where each expected answer comes from (not from the tools under test):
  ground_truth  the dataset's own symbol annotation (gt.pipe.json), counted by code
  printed       text printed on the sheet (OCR confirms it is there), read by eye on the overlay
  visual        checked on the overlay image: the highlighted route follows the drawn pipe
  honesty       the right answer is to decline: no scale, or no such tag; judged by wording
Two columns per question: `tool` = does the reference tool call return the expected result (index quality);
`agent` = does the agent's text answer match it. `grounded` = every node/pipe id the answer cites came from a tool.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pipes.agent_tools import call_tool
from pipes.tools import PipeIndex

HERE = Path(__file__).parent.parent
FILE = "pid_dataset/0__raw_data/sheets/test/{}.jpg"
WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
ID = re.compile(r"\b(?:node|pipe)_\d+\b")
METRES = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:m|metres|meters)\b", re.I)


def body(text: str) -> str:
    return text.split("Source:")[0]


def first_number(text: str) -> int | None:
    """First plain number in the answer; tokens with ids, tags or ranges (hyphen, underscore, quote, slash) skipped."""
    for tok in re.split(r"\s+", body(text)):
        t = tok.strip(".,;:()*")
        if re.search(r"[-_\"/]", t):
            continue
        if t.isdigit():
            return int(t)
        if t.lower() in WORDS:
            return WORDS[t.lower()]
    return None


def says_yes(text: str) -> bool:
    return bool(re.search(r"\byes\b|\bis connected\b|\bare connected\b", text, re.I)) and not re.search(
        r"not connected|isn't connected|no route|no path", text, re.I
    )


def declines_metres(text: str) -> bool:
    return not METRES.search(body(text)) and bool(re.search(r"scale|pixel|px|no length", text, re.I))


def says_none_found(text: str) -> bool:
    return bool(
        re.search(r"\bno\b|\bnone\b|not found|could not|couldn't|did not find|n't find", body(text), re.I)
    ) and not ID.search(text)


def cites_the_single_symbol(text: str, calls: list) -> bool:
    ids = {i for c in calls for i in c["result"].get("node_ids", [])}
    cited = set(ID.findall(text))
    return len(ids) == 1 and cited == ids


QUESTIONS = [
    {
        "id": "q01",
        "sheet": "157",
        "basis": "ground_truth",
        "question": "How many symbols are on sheet 157?",
        "expected": "46 (dataset annotation)",
        "tool": ("count_symbols", {}),
        "tool_ok": lambda r: r["answer"]["count"] == 46,
        "answer_ok": lambda t, calls: first_number(t) == 46,
    },
    {
        "id": "q02",
        "sheet": "157",
        "basis": "ground_truth",
        "question": "How many symbols are in the region x 1300 to 2200, y 200 to 1400 on sheet 157?",
        "expected": "32 (dataset annotation)",
        "tool": ("count_symbols", {"region": [1300, 200, 2200, 1400]}),
        "tool_ok": lambda r: r["answer"]["count"] == 32,
        "answer_ok": lambda t, calls: first_number(t) == 32,
    },
    {
        "id": "q03",
        "sheet": "157",
        "basis": "visual",
        "question": "Is node_8 connected to node_22 on sheet 157?",
        "expected": "yes, along the top and right-hand pipe (8 pipe segments)",
        "tool": ("path_between", {"a": "node_8", "b": "node_22"}),
        "tool_ok": lambda r: r["answer"]["connected"] is True,
        "answer_ok": lambda t, calls: says_yes(t),
    },
    {
        "id": "q04",
        "sheet": "157",
        "basis": "honesty",
        "question": "How long, in metres, is the pipe route from node_8 to node_22 on sheet 157?",
        "expected": "cannot give metres: the sheet is not to scale (about 879 px)",
        "tool": ("path_between", {"a": "node_8", "b": "node_22"}),
        "tool_ok": lambda r: (
            r["answer"]["connected"] and abs(r["answer"]["length_px"] - 879.2) / 879.2 < 0.02
        ),
        "answer_ok": lambda t, calls: declines_metres(t),
    },
    {
        "id": "q05",
        "sheet": "151",
        "basis": "printed",
        "question": "Which symbol carries the tag PV-10014A/B on sheet 151?",
        "expected": "exactly one symbol (the text PV-10014A/B is printed beside it)",
        "tool": ("count_symbols", {"tag": "PV-10014A/B"}),
        "tool_ok": lambda r: r["answer"]["count"] == 1,
        "answer_ok": cites_the_single_symbol,
    },
    {
        "id": "q06",
        "sheet": "151",
        "basis": "visual",
        "question": 'How many pipes carry the line number 4"-S1-P-10014 on sheet 151?',
        "expected": "3 pipe segments (one run: from P-101 along the top and down beside the tower)",
        "tool": ("pipe_length", {"tag": '4"-S1-P-10014'}),
        "tool_ok": lambda r: r["answer"]["n_edges"] == 3,
        "answer_ok": lambda t, calls: first_number(t) == 3,
    },
    {
        "id": "q07",
        "sheet": "151",
        "basis": "honesty",
        "question": 'What is the total length in metres of line 4"-S1-P-10014 on sheet 151?',
        "expected": "cannot give metres: not to scale, no written length (about 1282 px)",
        "tool": ("pipe_length", {"tag": '4"-S1-P-10014'}),
        "tool_ok": lambda r: (
            r["answer"]["length_m"] is None and abs(r["answer"]["length_px"] - 1282.2) / 1282.2 < 0.02
        ),
        "answer_ok": lambda t, calls: declines_metres(t),
    },
    {
        "id": "q08",
        "sheet": "176",
        "basis": "printed",
        "question": "How many symbols have a tag starting with P- on sheet 176?",
        "expected": "3 (P-1001, P-1002, P-1003 are printed on the sheet)",
        "tool": ("count_symbols", {"tag": "P-*"}),
        "tool_ok": lambda r: r["answer"]["count"] == 3,
        "answer_ok": lambda t, calls: first_number(t) == 3,
    },
    {
        "id": "q09",
        "sheet": "176",
        "basis": "printed",
        "question": "Which symbol is tagged TCU-1002 on sheet 176?",
        "expected": "exactly one symbol",
        "tool": ("count_symbols", {"tag": "TCU-1002"}),
        "tool_ok": lambda r: r["answer"]["count"] == 1,
        "answer_ok": cites_the_single_symbol,
    },
    {
        "id": "q10",
        "sheet": "176",
        "basis": "honesty",
        "question": "Is there a symbol tagged V-9999 on sheet 176?",
        "expected": "no such tag found (and no invented symbol id)",
        "tool": ("count_symbols", {"tag": "V-9999"}),
        "tool_ok": lambda r: r["answer"]["count"] == 0 and any("OCR" in c for c in r["caveats"]),
        "answer_ok": lambda t, calls: says_none_found(t),
    },
]


def run(tools_only: bool = False) -> list[dict]:
    from pipes.routing import route_page

    rows = []
    indexes: dict[str, PipeIndex] = {}
    for q in QUESTIONS:
        route = route_page(FILE.format(q["sheet"]))
        idx = indexes.setdefault(q["sheet"], PipeIndex.load(route["pipe"]))
        name, args = q["tool"]
        ref = call_tool(idx, name, args)
        row = {"id": q["id"], "sheet": q["sheet"], "basis": q["basis"], "question": q["question"],
               "expected": q["expected"], "tool": bool(q["tool_ok"](ref)), "reliable": ref["reliable"]}  # fmt: skip
        if not tools_only:
            from pipes.agent import answer_question

            r = answer_question(q["question"], FILE.format(q["sheet"]))
            calls = r["tool_calls"]
            seen = {
                i for c in calls for i in [*c["result"].get("node_ids", []), *c["result"].get("edge_ids", [])]
            }
            seen |= {i for c in calls for i in ID.findall(json.dumps(c["result"]["answer"]))}
            cited = set(ID.findall(r["answer"]))
            row |= {
                "answer": r["answer"], "agent": bool(q["answer_ok"](r["answer"], calls)),
                "grounded": cited <= seen, "tools_used": [c["tool"] for c in calls],
                "used_reference_tool": name in [c["tool"] for c in calls], "usage": r["usage"],
            }  # fmt: skip
        rows.append(row)
    return rows


def report(rows: list[dict]) -> str:
    agent = "agent" in rows[0]
    head = f"{'id':<4} {'sheet':>5} {'basis':<12} {'tool':>5} " + (
        f"{'agent':>6} {'grounded':>8} tools" if agent else ""
    )
    out = [head]
    for r in rows:
        line = f"{r['id']:<4} {r['sheet']:>5} {r['basis']:<12} {'ok' if r['tool'] else 'FAIL':>5} "
        if agent:
            line += f"{'ok' if r['agent'] else 'FAIL':>6} {'yes' if r['grounded'] else 'NO':>8} {','.join(r['tools_used'])}"
        out.append(line)
    n = len(rows)
    out.append(f"\ntool-level: {sum(r['tool'] for r in rows)}/{n}")
    if agent:
        out.append(
            f"agent answers: {sum(r['agent'] for r in rows)}/{n}; grounded: {sum(r['grounded'] for r in rows)}/{n}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m pipes.eval.agent_questions", description=__doc__)
    ap.add_argument("--tools-only", action="store_true")
    ap.add_argument("-o", "--out", default=str(HERE / "index_pipes" / "agent_eval.json"))
    a = ap.parse_args(argv)
    rows = run(a.tools_only)
    Path(a.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
