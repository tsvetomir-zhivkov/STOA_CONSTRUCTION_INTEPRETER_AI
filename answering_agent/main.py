"""Run: python -m answering_agent.main path/to/drawing.json"""
import argparse
import json
from pathlib import Path
from answering_agent.agent import answer_question


def load_document(path: Path) -> dict:
    with path.open(encoding='utf-8') as file:
        data = json.load(file)
    if not isinstance(data, dict) or data.get('schema_version') not in ('0.2', '0.3', 'piping-0.1'):
        raise ValueError('Unsupported drawing schema_version')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('document', type=Path)
    args = parser.parse_args()
    data = load_document(args.document)
    history = []
    while True:
        try:
            question = input('\nAsk about the drawing: ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in ('quit', 'exit'):
            break
        if not question:
            continue
        result = answer_question(question, data, history=history)
        print('\nAnswer:', result['answer'])
        history.extend([{'role':'user','content':question}, {'role':'assistant','content':result['answer']}])

if __name__ == '__main__':
    main()
