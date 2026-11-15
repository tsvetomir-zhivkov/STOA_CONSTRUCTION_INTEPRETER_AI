"""Bridge from `npm start` (scripts/start.mjs) to answering_agent.

  python scripts/agent_bridge.py <out/file.json>
  stdin:  {"question": "...", "history": [{"role": "user" | "assistant", "content": "..."}, ...]}
  stdout: {"answer": "...", "source_ids": [...]}   (answering_agent.agent.answer_question's result)

Runs from the repo root (start.mjs sets cwd), so `answering_agent` imports and its load_dotenv() finds .env.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))


def main(document: str) -> None:
    from answering_agent.agent import answer_question
    from answering_agent.main import load_document

    data = load_document(Path(document))
    msg = json.loads(sys.stdin.read() or "{}")
    result = answer_question(str(msg.get("question", "")), data, history=msg.get("history") or [])
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1])
