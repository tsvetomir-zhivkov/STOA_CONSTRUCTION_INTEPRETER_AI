import json
from agent import answer_question

with open("../sample_drawing.json", encoding="utf-8") as file:
    drawing_data = json.load(file)

while True:
    question = input("\nAsk about the drawing: ")

    if question.lower() in {"quit", "exit"}:
        break

    answer = answer_question(question, drawing_data)
    print("\nAnswer:", answer)
