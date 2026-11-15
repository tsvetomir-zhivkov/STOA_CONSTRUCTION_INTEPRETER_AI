# Stoa: the drawing is the evidence

Revision of the existing build, 2026-11-14. Based on the user's supplied specification and repository evidence. Composition choices are authored implementation decisions, not invented interview answers.

## Brief and authority

1. Vibe: the user asks for an "authentic, realistic architectural CAD drawing" and "restrained, mechanical, and precise" motion. Technical drafting, cool paper, ink, and red source references.
2. Journey: direct blueprint opening, separate foundation/floor/framing/roof/elevation sheets, slow perspective rotation, clean reassembly, closing CTA. Remove character, hands, drafting table, versions-on-layers narration, rotation narration, and house transformation.
3. Energy: calm opening, controlled separation, quiet inspection, registration climax, settled ending.
4. Memory: the same technical sheets close back into a source-tagged plan. The largest scroll allocation goes to registration.
5. Signature: five detailed sheets retain named reference markers while separating and rotating, then register into the floor plan. No blank replacement sheets.
6. Aesthetic: information-rich drawings within a restrained marketing composition. Two local font families: Bahnschrift (display/drawing lettering) and Segoe UI (prose). Six color roles.
7. Structure: opening, evidence passage, one shared technical stage spanning three beats, and closing plate. Revision of Plan-to-Evidence, not a new worldflight site.
8. Assets: repo-native SVG only. No API calls, raster generation, external fonts, or KIE. Artwork is explicitly illustrative, not claimed as an extracted construction document.

## Product evidence

- CLAUDE.md and translator/schema/drawing.schema.json define schema v0.2: one document per page or IFC storey; rooms, walls, openings, objects, texts, coverage, and provenance.
- blueprint-ingest/schema/blueprint.schema.json uses the same vocabulary. Its README separates deterministic geometry from semantic mapping.
- provenance.source_ref can be a DXF handle, PDF item index, IFC GlobalId, or extractor-local ID. Confidence is per entity, not an accuracy promise.
- rooms.type includes bedroom, living, kitchen, bathroom, corridor. openings.kind includes door, window, doorway. walls.is_load_bearing, walls.thickness_m, texts.role=dimension, and drawing.scale are valid fields.
- Foundation, framing, roof structure, and elevations are drawing subjects, not invented schema collections or structural-analysis promises.
- The implementation does not yet implement every planned route. No accuracy, speed, complete-coverage, or automatic 3D reconstruction claims.
- Closing CTA: "Explore the project", using the GitHub URL in the root package.json. The latest user direction removes this CTA and its description from the opening.

## Feeling curve (before the score)

1. Recognition: an enlarged blue architectural drawing with white outlines; the retained headline names the purpose.
2. Trust: geometry, source references, and coverage described using the actual contract.
3. Curiosity: the drawing opens into five widely spaced technical planes.
4. Scrutiny: quiet lateral camera shift and slow perspective rotation keep the sheets in view.
5. Satisfaction: the same planes align and the annotated floor plan resolves. This is the peak.
6. Resolve: persistent CTA on an ink ground.

Peak sentence: "All five drawings fitted back together, and every named reference was still attached to the plan."

Tell-someone sentence: "It's the site where a blueprint opens into its drawing sheets and returns as a plan you can trace."

Authored silence: separation and rotation have sheet titles and named callouts only. No explanatory headings or paragraphs. Final 12% of registration intentionally holds the resolved plan; no empty stage is intended.

## Grammar and fingerprint gate

Retain and refine **Plan-to-Evidence**: artifact first, short evidence passage, shared drafting stage, editorial action. In-flow identity and navigation leave the drawing unobstructed. Bans: middle-beat narration, decorative cards, invented model interfaces, footage, house morphs, per-act counters.

The eight stock grammars were checked: filmic one-shot requires a footage-led open; chaptered editorial forbids opening media; live surface implies an operating translator; continuous world requires geographic worldflight; typographic poster makes words the subject; gallery treats drawings as a catalog; split stage requires two persistent arguments; rhythmic cutlist conflicts with restrained inspection. The existing bespoke grammar fits this revision.

Registry contains only this site. The 4-of-6 new-site gate is not applicable to an in-place revision; do not claim a distinct new site. Preserve the historical row and append a revision record.

## Hero layer contract

Latest direction: keep "Drawings, made answerable", remove the opening description and project button, enlarge the blueprint, and use blue paper with white outlines. The headline is a compact single line on desktop and two lines on phones. The centered CAD sheet occupies the released space and fits within the initial viewport. The same blue/white drawing palette carries through separation and reassembly. This supersedes the earlier compact text-row opening.

| Plane | Movement | Constraint |
|---|---|---|
| Registration grid | Small parallax | No interference with text |
| Under-sheet | Independent shallow translation | Paper edge, no drafting table |
| Main CAD drawing | Restrained translation | Complete subject visible |
| Source overlay | Attached to drawing | All provenance circles named |
| Typography | Ordinary flow | Retained headline; no opening description or CTA |

Mobile: separate square drawing, larger labels, portrait stack, smaller angles. Reduced-motion and no-JS: static readable sheets and final annotated plan in ordinary flow.

## Scroll score

| Beat | Engine act | Primary device family | Allocation |
|---|---|---|---|
| Blueprint opening | pin | parallax | 1.3 viewports |
| Source contract | flow | reveal | about 0.65 viewport |
| Separation | pin | pin / displacement | 1.5 viewports of travel |
| Inspection | pan | lateral pan / rotation | 1 viewport of travel |
| Registration peak | pin | reveal / CAD resolve | 2.5 viewports of travel + final viewport |
| Commitment | flow | flow + in | about 0.8 viewport |

Primary families: parallax > reveal > pin > pan > reveal > flow. Five families; no adjacent repeat. Engine act order: pin > flow > pin > pan > pin > flow, also without adjacent repetition. Middle timeline sections overlap by one viewport so the shared sticky stage stays in place. Bespoke choreography reads --sc-p; engine unchanged. Actual measured spans belong in VERIFICATION.md.

## Verification

Run unmodified shoot.mjs on final desktop, portrait, compact phone, and reduced-motion files. Inspect sheets and full-size states. Functional checks cover labels, transformed sheet bounds and gaps, continuity, keyboard, links, no-JS/reduced-motion, no external requests, and engine hashes. Preserve earlier evidence and failed passes.

