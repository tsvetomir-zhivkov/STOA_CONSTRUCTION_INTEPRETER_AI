# Project Proposal: Grounded Q&A over Construction Drawings

**Event:** Junction 2026 hackathon (48 hours), Stoa challenge
**Working title:** Drawing-to-graph question answering with visual citations

---

## 0. Context for the reader

This document is self-contained. It describes a hackathon project proposal developed through a design interview.

**The challenge (from Stoa, a construction-tech startup):** Build a program that answers natural-language questions about construction drawings and building information models (BIM), accurately and verifiably. A great answer is correct and shows where in the drawing it came from, so a human can check it in seconds. Stoa wants solutions that next year's frontier AI models will not make obsolete, for example hybrids that combine frontier models with computer vision, geometry, 3D or small fine-tuned models.

**Judging criteria (25% each):**
1. Accuracy of answers
2. How well the solution visualizes its reasoning so a human can verify the answer
3. Creativity
4. Low inference cost

**Data available so far (preview set):**
- CubiCasa5K: 241 PNG floor plans of apartments (modern, mostly Finnish; the full public dataset has polygon annotations of rooms, walls, doors and windows).
- Europeana: 61 JPG historical architectural drawings.
- Finna: 95 JPG historical architectural drawings (Finnish archives).
- No PDFs, no BIM files and no example questions have been released yet. The full dataset may include more formats and languages.

---

## 1. Problem and value

Site engineers spend hours every day looking for facts buried in drawings: how big a room is, which rooms connect, how many of something there are, what an abbreviation means. General-purpose AI models misread technical drawings (they miscount, invent dimensions, confuse symbols) and they cannot show where an answer came from, so engineers cannot trust them.

**Value proposition:** Ask a question in plain language and get an answer in seconds, with the exact part of the drawing highlighted so it can be verified at a glance. It is cheap to run because the expensive reading of the drawing happens once, not on every question.

---

## 2. User and use cases

**Primary user:** a site engineer (or site manager) on a construction project, working from a laptop or tablet with a set of drawings.

**MVP question focus: rooms, labels and spatial relationships.** Example questions:
- "What is the area of the living room?"
- "How many bedrooms are there?"
- "Which rooms can I reach directly from the hallway?"
- "Which room is next to the kitchen?"
- "What does 'MH' mean on this plan?" (Finnish: makuuhuone = bedroom)
- "What is the total area of all wet rooms?"
- "Is the written area of the sauna consistent with its drawn size?"

Out of MVP scope: structural, MEP (mechanical, electrical, plumbing), quantities of materials, cross-sheet detail references at scale. These are future work.

---

## 3. End-to-end flow

1. **Upload:** the user loads a drawing (image now; PDF later).
2. **Global pass:** one cheap model call classifies the drawing type (modern floor plan, historical drawing, other) and locates the scale note, legend and annotation regions. It routes the drawing to the right pipeline.
3. **Extract once (the "translator"):** a pipeline reads the drawing and writes a structured JSON index: rooms, walls, doors and windows as polygons, with names, areas and coordinates. It also writes a **coverage map** recording which regions and element categories were analysed, and with what confidence. This happens once per drawing and is cached.
4. **Ask:** the user types a question.
5. **Answer (the "agent"):** a small, cheap language model plans which tools to call on the JSON index (find rooms, compute area, find adjacent rooms, look up an abbreviation). Code does all math and geometry. Every tool result carries the coordinates of the entities it used.
6. **Gap handling (the "living index"):** if the index has nothing relevant and the coverage map shows that region or category was not reliably analysed, the agent crops that region of the drawing and asks a general vision model to read it. The result is written back into the index, so the next question about that area is cheap.
7. **Show:** the answer appears beside the drawing with the cited rooms, doors and walls highlighted (outlines, shading, door markers). Warnings such as "check this" or "lower confidence" are shown inline.

---

## 4. Architecture

The system has two stages: an offline **vision translator** and an online **answering agent**. The translator starts with a light global pass.

### 4.0 Global pass (one cheap call per drawing)

Before detailed extraction, one cheap vision-model call looks at a downscaled version of the whole drawing and returns:
- the drawing type (modern floor plan, historical drawing, section/elevation, other),
- the bounding boxes of the scale note, legend, title block and main annotation areas,
- which pipeline to use: the floor-plan pipeline (Section 4.1) or the vision fallback (Section 5.6).

This is a planning and routing step, not a full agent. It gives the system a global view of the drawing at very low cost and tells later steps where to look for the scale and legend.

### 4.1 Vision translator (runs once per drawing)

A pipeline of specialist tools, not one general vision-language model:

| Step | Tool | Output |
|---|---|---|
| Layout segmentation | Floor-plan segmentation model pretrained on CubiCasa5K | Polygons for rooms, walls, doors, windows |
| Text reading | OCR (PaddleOCR or Tesseract) | Text boxes: room labels, written areas, dimensions, scale notes |
| Naming and linking | Cheap LLM plus the conventions glossary | Each room polygon gets a name and type by matching OCR labels inside it (e.g. "MH 12.5 m²" → bedroom, written area 12.5 m²) |
| Scale recovery | Code | Pixels-to-metres factor (see Section 5.4) |

**Output JSON (per drawing):**
- `sheet_id`, `level_id` (floor), image size, scale factor and its source/confidence
- `rooms[]`: id, name, type, label text, polygon in page pixel coordinates, polygon in metres, written area, computed area, flags
- `walls[]`, `doors[]`, `windows[]`: id, geometry, which rooms they touch
- `texts[]`: raw OCR boxes, including ones not yet linked to anything
- `edges[]`: only references and legend meanings (see Section 5.2)
- `coverage[]`: for each region of the drawing and each element category (rooms, doors, windows, text, symbols), whether it was analysed, by which extractor, and with what confidence
- Every entity carries `source` (which extractor produced it) and `confidence`
- Empty `height` / `elevation` fields so the same structure can be extruded to 3D later

**One standard schema for all extractors.** The segmentation model, OCR, the LLM namer and the vision-model fallback all write this same JSON schema. Any extractor can be replaced by a better one without changing the answering agent, the tools or the UI.

### 4.2 Answering agent (runs per question)

A small LLM (Haiku-class) with tool calling. Tools:

- `find_rooms(type | name | label)` → matching rooms with ids and polygons
- `room_area(room_id)` → written and computed area, mismatch flag, polygon
- `rooms_adjacent_to(room_id, via="wall"|"door")` → neighbouring rooms and the shared wall/door geometry
- `glossary_lookup(term, language)` → meaning of an abbreviation or symbol
- `look_at_region(bbox, question)` → fallback: sends a crop to a general vision model; result is written back into the index

**Rules:**
- The LLM never does arithmetic or geometry; code does.
- Every tool returns coordinates. The final answer must cite at least one entity id; those ids drive the highlighting.

### 4.3 Living index and gap detection

**Proactive coverage map.** During extraction the translator records, for each region and element category, whether it was analysed and how confidently (see `coverage[]` above). Uncertain or unanalysed areas are known from the start, not only discovered when someone asks. Low-coverage regions are also shown in the UI.

**Rule:** the system never says something does not exist ("there is no staircase") unless the coverage map shows that region and category were analysed with good confidence. Otherwise it says it is not sure and looks.

A gap is detected in two ways:
1. **Coverage check:** the question asks about an entity type or region where the index has nothing, and the coverage map shows that region or category was not reliably analysed (e.g. "where is the fireplace?", no fireplace entity exists, and symbols were not covered).
2. **Citation rule:** if the agent cannot cite any node for its answer, that is treated as a gap rather than a guess.

On a gap, `look_at_region` sends only the relevant crop to a general vision model. Its finding is stored as a new entity in the same schema (with its bounding box and a "found by fallback" source tag), and the coverage map is updated for that region. The index improves with use, and the expensive model is called only where the cheap pipeline failed.

### 4.4 Verification UI

The answer is shown next to the drawing with the evidence highlighted: cited room outlines and shading, door markers for connections, and the OCR text box that supplied a written value. Each cited item is clickable. Warnings appear as badges: "check this" for area mismatches, "lower confidence" for fallback or historical drawings.

---

## 5. Why this representation

### 5.1 Extract once, answer cheaply
Reading a drawing with a large vision model on every question is slow, expensive and not reproducible. Extracting once into structured data makes each question cost a few cents or less (a small LLM plus deterministic code) and makes answers repeatable. This directly targets the "low inference cost" criterion.

### 5.2 Geometry first, minimal graph
Containment (what is inside which room) and adjacency (which rooms share a wall or door) are **computed from polygons on demand** with a geometry library, not stored as edges. Computed relations cannot go stale and cannot be hallucinated. Only things geometry cannot derive are stored as graph edges: cross-references (e.g. "see detail 3 on sheet A-201") and legend meanings (a symbol means "floor drain").

### 5.3 Coordinates everywhere, one model-agnostic schema
Every entity keeps its page coordinates, so every answer can be traced back to pixels on the drawing. This is what makes visual verification possible.

The schema is also model-agnostic: every extractor writes the same standard JSON, with its source and confidence. When next year's models are better, they replace an extractor (for example the segmentation model or the fallback vision model) and the rest of the system keeps working, and gets more accurate. This is our direct answer to Stoa's request for a solution that next year's frontier models won't make obsolete: better models make the system better instead of replacing it. The parts that stay valuable are the representation, the geometry tools, the coverage map and the verification UI.

### 5.4 Areas and scale
- **Scale recovery**, in order of preference: an explicit scale note (e.g. 1:100, located by the global pass), then dimension lines (a known length in mm or m against its length in pixels), otherwise a low-confidence estimate based on typical door widths. The scale source and confidence are shown with every computed measurement.
- **Written room areas are not used to calibrate the scale**, because they may be wrong or rounded. They are used only to cross-check.
- When a room has a **written area**, it is reported as written on the drawing. The **computed area** (from the polygon and scale) is compared with it. A mismatch beyond about 10% is flagged "check this". This doubles as a **drawing audit** feature (it can catch drawing errors), with the honest caveat that the mismatch may be our own extraction or scale error.

### 5.5 Conventions glossary instead of "learning from books"
The original idea included a stage where the system learns drawing conventions from textbooks. For the MVP this is replaced by a **curated multilingual conventions glossary** (room abbreviations and common symbols, e.g. MH = makuuhuone = bedroom, OH = olohuone = living room, KH = kylpyhuone = bathroom, S = sauna, ET = eteinen = hall, K = keittiö = kitchen, VH = vaatehuone = walk-in closet). It is exposed as an agent tool and used by the translator when naming rooms. Learning conventions from books is future work.

### 5.6 Historical drawings
Europeana and Finna drawings are hand-drawn, scanned and stylistically different from what the segmentation model was trained on. They are routed through the vision-model fallback, with the same highlighting UI, and answers are flagged as lower confidence.

---

## 6. MVP scope (48 hours)

**In scope:**
- CubiCasa-style modern floor plans (PNG).
- Global pass: drawing type, scale/legend locations, routing.
- Translator pipeline: segmentation + OCR + LLM naming + scale recovery → JSON in one standard schema, with a coverage map.
- Agent with the five tools above.
- Living index with fallback write-back.
- Viewer with highlighted evidence and warning badges.
- Area mismatch flag (drawing audit).
- Glossary for Finnish/Swedish/English room terms.
- Historical drawings via the fallback path, flagged lower confidence.
- Evaluation with accuracy and cost-per-question numbers.

**Stretch goal (about 2 hours):** extrude walls of one floor plan into a simple 3D view, using the empty height fields in the JSON.

### Evaluation plan
- **Hand-checked test set:** 30 to 50 questions over a handful of plans, answers verified by hand.
- **Auto-generated test set:** questions generated from CubiCasa annotations (room counts, room types, adjacency, areas), giving hundreds of questions with known answers.
- **Pitch metrics:** accuracy on both sets, and average cost and latency per question, compared with asking a general vision model directly.

---

## 7. Technology, datasets and challenges

**Proposed stack:**
- Python backend
- CubiCasa5K pretrained floor-plan segmentation model
- PaddleOCR or Tesseract for text
- Shapely for polygons, areas, containment and adjacency
- Haiku-class small LLM for naming and the answering agent; a stronger vision model only for the fallback
- Streamlit (fastest) or a React viewer (more polished) for the UI
- JSON files as the index store (one per drawing)

**Datasets:** CubiCasa5K (primary, has ground-truth annotations for evaluation), Europeana and Finna historical drawings (fallback path), plus whatever Stoa releases during the event.

**Technical challenges:**
- Segmentation quality on plans that differ from training data.
- OCR on small, rotated or overlapping text.
- Linking OCR labels to the right polygon when labels sit near walls.
- Recovering a reliable scale.
- Keeping cost low while still handling gaps well.

---

## 8. MVP versus future work

| Area | MVP | Future |
|---|---|---|
| Inputs | PNG/JPG floor plans | Multi-page PDFs, sections, elevations, IFC/BIM models |
| Questions | Rooms, labels, areas, counts, connections | Structure, MEP, materials, quantities, schedules |
| Conventions | Curated glossary | Learned from textbooks and standards; per-company conventions |
| Cross-references | Stored edges, basic | Full multi-sheet navigation ("see detail 3/A-201") |
| 3D | Optional wall extrusion of one plan | Full 3D model linked to drawings and BIM |
| Index | Living JSON per drawing | Project-wide graph across all sheets and revisions |
| Audit | Area mismatch flag | Consistency checks across sheets and revisions |
| Relationships | Computed from geometry; only references and legend meanings stored | Richer graph: cross-sheet links, multi-floor links (e.g. stairs), structural and MEP relationships |

**Considered and deferred.** These ideas came up in design review and are good long-term directions, but were judged too costly to build and evaluate in 48 hours:
- A richer knowledge graph with stored relationships across sheets, floors, structure and MEP.
- A spatial database instead of JSON files.
- A multi-agent framework with specialist agents per level of detail (whole building, regions, components).
- Extra detectors such as YOLO (symbol and object detection) and SAM (general segmentation).

The standard schema and the tool-based agent are designed so these can be added later as new extractors or new tools without rebuilding the system.

---

## 9. Risks and assumptions

**Risks:**
- **Pretrained model on new plans:** the CubiCasa model may perform worse on the preview plans or on Stoa's real drawings than on its own test set. Mitigation: fallback path, citation rule, and measuring accuracy early.
- **OCR on small text:** labels and numbers may be missed or misread. Mitigation: upscaling crops, glossary-based correction, fallback on gaps.
- **Scale recovery:** wrong scale breaks every computed area. Mitigation: use scale notes and dimension lines first, show scale source and confidence, cross-check against written areas and flag mismatches.
- **Missed elements:** the pipeline may fail to detect something (e.g. a staircase). Mitigation: the coverage map marks uncertain regions, and the system never claims absence without coverage.
- **Stoa's real questions may differ** from our MVP focus (rooms and spatial). Mitigation: the tool-based agent is easy to extend with new tools once example questions are released.
- **Time:** 48 hours is tight. Mitigation: build the end-to-end thin slice first (one plan, one question, highlighted answer), then widen.

**Assumptions:**
- Most evaluation drawings will be floor plans similar to CubiCasa5K.
- Written areas and room labels are present on many drawings.
- A small LLM is good enough for tool planning when code does the math.
- Judges value a verifiable, cheap, slightly narrower system over a broad but unverifiable one.

---

## 10. One-paragraph summary

A cheap global pass classifies each drawing and finds its scale and legend. We then turn the drawing, once, into a structured index of rooms, walls and doors with coordinates and real-world units, using a cheap pipeline of a specialist floor-plan model, OCR and a small LLM with a multilingual conventions glossary. Every extractor writes the same standard schema plus a coverage map of what was analysed, so models can be swapped as they improve and the system never claims something is absent from a region it did not check. A small LLM agent answers questions by calling geometry tools on that index, so code does the math and every answer cites exact entities that are highlighted on the drawing. When the index has a gap, the agent looks at just that region with a general vision model and writes the finding back, so the index improves with use. Written areas are cross-checked against drawn geometry to flag possible drawing errors. The result is accurate, verifiable in seconds, cheap per question, and built around a representation that gets better, not obsolete, as models improve.
