# CAD revision verification

Build: stoa-plan-to-evidence. Local preview: http://127.0.0.1:4500. Revision date: 2026-11-14.

## Latest follow-up: enlarged blue blueprint

The opening now retains only "Drawings, made answerable." above the centered drawing. Its description and project button are removed. The blueprint is substantially larger: about 1071px wide at 1440x900 and 800px at 1280x720, compared with approximately 880px and 607px in the previous revision. Phone drawings use nearly the full viewport width. The complete drawing fits on the first screen at all five tested sizes.

All eight native CAD SVGs were rebuilt with blue paper (#164E91), white outlines, white named references, and softer white drafting detail. Six theme tokens and two font families remain. The external sheet callout leaders use a white outline to stay visible across both blue paper and the pale background. No external assets or APIs were used; engine hashes remain unchanged.

Final unmodified `shoot.mjs` passes: [desktop](lab/blueprint-blue-desktop-final/report.json), [portrait](lab/blueprint-blue-mobile-final/report.json), [compact](lab/blueprint-blue-compact-final/report.json), and [reduced motion](lab/blueprint-blue-reduced-final/report.json). All four exited successfully, covering 113 screenshots, with no dead-scroll findings, browser errors, or failed requests. Measured copy cues passed 4.5:1 contrast. The full-page contact sheets and opening compositions were visually reviewed. The [functional report](lab/cad-functional/report.json) passes at 1440x900, 1280x720, 768x1024, 390x844, and 360x640, with checks for opening copy removal, larger visible drawing bounds, five keyboard stops, source labels, phase continuity, and static fallbacks.

The intended recognition beat is now carried by the enlarged blue drawing; the quieter separation/inspection beats and the annotated reassembly peak remain intact. [Source hashes for this revision](lab/blueprint-blue-source-hashes.json) supersede earlier hashes for modified files. Physical-device and other-browser testing remain unverified. Sections below retain the earlier revision history.

## Previous follow-up: centered opening blueprint

The user's visibility correction replaces the side-by-side opening with a centered drawing beneath a compact headline/subcopy/CTA row. Drawing width is constrained by both viewport width and height. Phone layout starts near the top rather than distributing excess space above the drawing. The plan is fully inside the first viewport and horizontally centered in checks at 1440x900, 1280x720, 768x1024, 390x844, and 360x640. The functional script now asserts those bounds as well as avoiding copy overlap.

The four fresh `shoot.mjs` runs passed: [desktop](lab/hero-centered-desktop/report.json), [portrait](lab/hero-centered-mobile/report.json), [compact](lab/hero-centered-compact/report.json), and [reduced motion](lab/hero-centered-reduced/report.json). Together they contain 113 sampled states, with no dead scroll and all measured copy cues above 4.5:1. Contact sheets and full-size opening screenshots were reviewed. The [functional report](lab/cad-functional/report.json) also passed across all five sizes and both static fallbacks. The six-beat journey and its reassembly climax remain intact; the opening now establishes the drawing as the central subject.

Current SHA-256: `site.css` = `78DE58AC5B7D3F2556F51AA59D6D260DC2E6A678CDA1B24433EABD2F34476E95`; `scripts/functional-check.mjs` = `D6315D94F7AAF214672F0A14BF2C4938EFFD40E3B4F66D54CC5BA1856C4093F4`. Other production files retain the hashes recorded below. Earlier evidence is preserved as the preceding CAD revision, not as verification of this follow-up. Physical-device and browser-engine limits still apply.

## What changed

The existing site now opens on a detailed architectural drawing with a two-line headline and the existing project CTA. The vector set uses cut-wall hatching, correct hinge-centred door arcs, mullions, grid bubbles, extension lines, dimensions, a north arrow, a dimensionally consistent graphic scale, and formal title blocks. The portrait source plan has its own larger callout layout.

The same five SVG sheets occupy a shared sticky stage for separation, perspective rotation, and reassembly. The phase boundaries do not replace the sheets or unpin the stage. Labels are projected from drawing coordinates and remain face-on. The final annotated floor plan is the climax. There is no character, hand, drafting table, versions-on-layers copy, rotation narration, or 3D house transformation in the served page.

Copy and terminology were checked against CLAUDE.md, translator/schema/drawing.schema.json, translator/__init__.py, blueprint-ingest/schema/blueprint.schema.json, blueprint-ingest/README.md, and the product planning documents. Drawing subjects such as foundation and roof are not presented as new schema entity collections. Illustrations are marked as authored samples.

## Design record

- Grammar: refined Plan-to-Evidence. Alternatives and structural bans are recorded in BRIEF.md.
- Primary devices: parallax > reveal > pin > pan > reveal > flow.
- Engine acts: pin > flow > pin > pan > pin > flow.
- Five device families, no adjacent repetition, no video.
- Declared pinned spans: hero 1.3, separation 2.5, rotation 2.0, registration 3.5 viewport-heights. Middle acts overlap by one viewport to share the stage. Registration owns 2.5 viewports of actual pinned travel, the largest span.
- Measured page lengths: 8.46 viewports at 1440x900, 8.59 at 390x844, 8.77 at 360x640, 8.47 at 768x1024.
- Six color tokens; Bahnschrift and Segoe UI, with system fallbacks. No webfont dependency.
- All choreography reads engine --sc-p. Paper dimensions stay constant during scrolling; scaling, translation, rotation, opacity, and the final clip-path reveal provide motion.
- API generation calls and spend: zero.

## Functional evidence

scripts/functional-check.mjs writes lab/cad-functional/report.json and representative opening, separated, resolved, and fallback screenshots.

Checks include:

- Two-line headline and visible opening CTA at desktop, portrait, compact-phone, and tablet sizes.
- Every source marker has an attached nonempty name; six named references in each annotated SVG.
- Five sheets, their captions, and their callouts remain within the viewport during rotation.
- No overlap between separated paper bounds or between callouts and neighbouring captions.
- Minimum sampled paper separation is about 94px on desktop, 37px on the portrait phone, 23px on the compact phone, and 51px on tablet.
- Phase-boundary displacement stays below 0.06px at the tested boundaries.
- Six keyboard stops remain visible with focus outlines. Internal navigation reaches the evidence section.
- Primary links retain the repository destination; the schema link targets the schema file.
- No broken images, horizontal document overflow, browser errors, or external page-load requests.
- Reduced-motion and JavaScript-disabled content remain reachable. A live reduced-motion preference change at the climax restores the drawing set.
- Both engine copies match the skill originals byte for byte.

External destinations were checked against repository configuration and anchor markup; no claim of testing GitHub availability is made.

## Harness and visual review

The unmodified skill shoot.mjs is used for all harness passes. Each normal-motion pass samples 34 states. Evidence is retained in lab/, including unsuccessful first passes.

All four final runs exited successfully. The 113 final screenshots were visually reviewed through all four contact sheets, with representative states also inspected at full size. No final run reported dead scroll, browser errors, or failed requests. All measured copy cues exceeded the harness's 4.5:1 contrast threshold.

| Final run | Viewport | Samples | Lowest measured cue contrast | Evidence |
| --- | --- | --- | --- | --- |
| Desktop | 1440 x 900 | 34 | 10.77:1 | [Report](lab/cad-desktop-final/report.json), [contact sheet](lab/cad-desktop-final/sheet.png) |
| Portrait phone | 390 x 844 | 34 | 12.31:1 | [Report](lab/cad-mobile-final/report.json), [contact sheet](lab/cad-mobile-final/sheet.png) |
| Compact phone | 360 x 640 | 34 | 12.31:1 | [Report](lab/cad-compact-final/report.json), [contact sheet](lab/cad-compact-final/sheet.png) |
| Reduced motion | 1440 x 900 | 11 | 11.14:1 | [Report](lab/cad-reduced-final/report.json), [contact sheet](lab/cad-reduced-final/sheet.png) |

The [functional report](lab/cad-functional/report.json) records additional tablet, keyboard, geometry, live motion-preference, and JavaScript-disabled checks. Contrast measurements above apply to the copy cues measured by the harness, not every fine drafting line or small title-block inscription.

[Final source SHA-256 hashes](lab/final-source-hashes.json) identify the HTML, CSS, choreography, SVG generator, and unchanged engine copies used for these passes. The engine hashes are:

- scrollcraft.js: `91a8e57cec07b7b977584885741f07d93fd1cd08fdbea623d5432fcede66fd38`
- scrollcraft.css: `519e254c1877a50eb724153d483ccff5b3b434fbaf3baf9c5bc1179ec28b075a`

## Findings and fixes

1. Existing build: oversimplified layer illustrations, placeholder reassembly planes, mobile callout clipping, excessive headline wrapping, and an incomplete device-family count. Rebuilt the drawings and the shared choreography.
2. First CAD pass (cad-desktop-r1 / cad-mobile-r1): inappropriate use of the engine's world-copy attribute hid the opening and evidence headings. Replaced it with explicit visible cue windows. The shared stage's entrance was not included in dead-scroll state; added stage-position reporting.
3. Portrait review: labels collided with neighbouring sheet captions. Labels now share a face-on band above each plane on phones.
4. Compact check: tilted sheet bounds overlapped. Reduced phone yaw and responsive paper size; measured the resulting gaps.
5. Tablet check: the hero headline wrapped to three lines. Stacked its copy in the tablet composition.
6. Motion audit: replaced changing paper layout dimensions with composited scale; delayed callout appearance until the separating sheets have room; lightened compounded paper shadows.
7. Preference audit: switching to reduced motion after the climax inherited a hidden sheet-group opacity. Static layout now restores the group, and static copy remains visible outside its former cue range.

## Feel check

The intended curve is recognition > trust > curiosity > scrutiny > satisfaction > resolve. The first visual review began with absence instead of recognition because the hero text was hidden, and the portrait inspection felt crowded. After those fixes, the final contact-sheet review shows a complete source-led opening, a short factual passage, spacious separation, a quieter inspection beat, one clear reassembly peak, and a closing CTA that holds.

The first pass is not counted as successful evidence. The final source plan replaces the empty-paper climax from the old build. Rotation is intentionally quieter than reassembly; it carries no body narration.

## Fingerprint

This is an in-place revision of the only existing registered site. The 4-of-6 gate for distinct new sites does not apply. FINGERPRINTS.md preserves the historical row and records the current revision rather than claiming a different new site.

## Limits

Verification uses installed headless Chrome. Physical iPhone/Android touch scrolling and other browser engines have not been tested. No public deployment was requested. The existing localhost server was reused and verified by the returned page title; it was not an agent-owned process to stop.

## Reproduce

From this build directory:

- npm run build regenerates the native SVG set.
- npm run preview serves the build on port 4500.
- npm run check runs functional verification.
- node ../../../.agents/skills/scroll-craft/scripts/shoot.mjs --url http://127.0.0.1:4500 --out lab/recheck
- Add --width 390 --height 844 for portrait, --width 360 --height 640 for compact, or --reduced-motion for the static composition.

