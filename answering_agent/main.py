"""Run with python -m answering_agent.main <building.json>."""

import argparse
from pathlib import Path

from translator.store import read_drawing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, help="Validated v0.3 building or legacy v0.2 JSON")
    args = parser.parse_args()
    data = read_drawing(args.document)
    from answering_agent.agent import answer_question

    while True:
        try:
            question = input("\nAsk about the building: ")
        except EOFError:
            break
        if question.lower() in {"quit", "exit"}:
            break
        print("\nAnswer:", answer_question(question, data))


if __name__ == "__main__":
    main()
