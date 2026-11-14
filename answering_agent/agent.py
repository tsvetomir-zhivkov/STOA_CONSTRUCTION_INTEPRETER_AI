"""Gemini answering agent for Stoa drawing-index schema v0.2.

Install: uv add google-genai python-dotenv shapely
Set GEMINI_API_KEY in .env and optionally GEMINI_MODEL.
"""
from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from tools import (
    aggregate,
    connected_rooms,
    check_coverage,
    detect_gap,
    geometry_operation,
    get_entity,
    query_entities,
)

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
SYSTEM_INSTRUCTION = """You are a construction drawing assistant for engineers.
Use deterministic tools for drawing facts, counts, measurements, and geometry.
The drawing index uses schema v0.2; ignore superseded entities.

COVERAGE AND GAP DETECTION:
- The index may be incomplete. Before concluding something is absent, call
  check_coverage_tool for the relevant category and region.
- An empty search is NOT proof of absence. Only say an element is absent when
  relevant coverage explicitly verifies absence.
- Coverage confidence on 'partial' or 'not_analysed' records does not establish
  completeness, regardless of how high the confidence value is.
- If evidence is missing, call detect_gap_tool with the relevant category,
  any supporting entity IDs, and region when known.
- If detect_gap_tool reports a gap, do not guess. Explain briefly that the
  available drawing information is insufficient.
- There is no vision fallback tool yet. Do not claim to inspect image crops,
  discover new entities, or update the index.
- For a verified absence, cite the coverage reference rather than making up
  an entity ID. Do not confuse lack of evidence with evidence of absence.
- Do not infer room access from touching polygons.

RESPONSE STYLE:
- Answer directly, naturally, and concisely; 1-3 sentences for simple questions.
- Do not expose JSON keys, coordinates, raw provenance, or tool implementation.
- Do not list unrelated entities or explain tool calls.
- Include supporting active entity IDs separately: 'Source: room_1'.
- For verified absence, use 'Source: coverage[0]' (actual tool reference).
- If coverage is incomplete, give one brief warning, such as:
  'Note: The drawing has only been partially analysed.'
- Never present incomplete results as exhaustive or invent citations.

LANGUAGE:
- Support English, Finnish, French and Swedish.
- Detect the language of the user's question.
- Always answer in the same language as the question,
  unless the user explicitly requests another language.
- Understand construction terminology in all three languages.
- Match equivalent concepts across languages.
  Examples:
  "bedroom" = "makuuhuone" = "sovrum" = "chambre"
  "kitchen" = "keittiö" = "kök" = "cuisine"
  "bathroom" = "kylpyhuone" = "badrum"
  "living room" = "olohuone" = "vardagsrum"
- Use the JSON's canonical entity types when searching,
  regardless of the user's language.
- Translate descriptions naturally, but preserve
  measurements, entity IDs, and source references.
- Never translate JSON keys or modify the schema.
"""


def answer_question(question: str, drawing_data: dict[str, Any]) -> str:
    """Answer one question using deterministic drawing tools and Gemini planning."""
    if drawing_data.get("schema_version") != "0.2":
        raise ValueError("This agent expects Stoa drawing-index schema v0.2")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY in your .env file")

    # Wrappers bind the drawing data privately; the model never supplies it.
    def query_entities_tool(collection: str, field: str = "", value: str = "", limit: int = 50) -> dict:
        """Search active drawing entities. Collections: levels, units, rooms, walls,
        openings, objects, texts, edges. Optional exact-match field/value filter.
        Use room 'type' (e.g. bedroom, living, wc, entry), opening 'kind'
        (door, window, doorway), or object 'category'.
        """
        return query_entities(drawing_data, collection, field, value, limit)

    def get_entity_tool(entity_id: str) -> dict:
        """Get full indexed entity details, including geometry, measurements,
        confidence, status and provenance, by entity ID.
        """
        return get_entity(drawing_data, entity_id)

    def aggregate_tool(collection: str, operation: str, measure: str = "area_m2", field: str = "", value: str = "") -> dict:
        """Count or aggregate active entities, optionally filtering by exact field/value.
        Operations: count, sum, min, max, average. For numeric operations,
        choose a measurement such as area_m2, width_m, height_m or length_m.
        Missing numeric values are excluded and reported separately.
        """
        return aggregate(drawing_data, collection, operation, measure, field, value)

    def geometry_operation_tool(first_id: str, second_id: str, operation: str) -> dict:
        """Compute a polygon relation between two indexed entities by ID.
        Operations: distance, touches, intersects, contains.
        Distance uses metres if both have metric polygons, otherwise pixels.
        """
        return geometry_operation(drawing_data, first_id, second_id, operation)

    def connected_rooms_tool(room_id: str, kind: str = "door") -> dict:
        """Find rooms explicitly connected to a room through indexed openings.
        Kinds: door, doorway, window. Requires opening.room_ids data.
        """
        return connected_rooms(drawing_data, room_id, kind)

    def check_coverage_tool(category: str, region: list[float] | None = None) -> dict:
        """Check whether a drawing element category was reliably analysed.
        Category examples: rooms, doors, windows, walls, fixtures, symbols,
        fireplace, stairs, text. Region is an optional pixel bounding box
        [left, top, right, bottom]; omit for the whole page. Only
        absence_verified=true allows a negative claim about that scope.
        """
        return check_coverage(drawing_data, category, region)

    def detect_gap_tool(category: str, entity_ids: list[str] | None = None,
                        region: list[float] | None = None) -> dict:
        """Evaluate evidence for an answer about a category and optional pixel
        region. Supply supporting entity IDs if available. Returns
        supported_entities, verified_absence, or gap. A gap means further
        extraction is needed; no image fallback is currently available.
        """
        return detect_gap(drawing_data, category, entity_ids, region)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[
                query_entities_tool,
                get_entity_tool,
                aggregate_tool,
                geometry_operation_tool,
                connected_rooms_tool,
                check_coverage_tool,
                detect_gap_tool,
            ],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=12,
            ),
            thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
            temperature=0,
        ),
    )
    if response.usage_metadata:
        usage = response.usage_metadata
        print(
            "Tokens (final response usage): "
            f"input={usage.prompt_token_count}, "
            f"output={usage.candidates_token_count}, "
            f"thinking={usage.thoughts_token_count}"
        )
    return response.text or "No text answer was returned."
