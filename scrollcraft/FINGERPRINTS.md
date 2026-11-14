# Fingerprints

Every site you build with **scroll-craft** gets one row here, appended after it
ships. The registry exists so your next build can prove it is a different page
rather than a re-skin of one you already made.

This file is **yours**. It starts empty on purpose: the gate is about not
repeating *yourself*, so it has nothing to say until you have built something.

The rules and the gate live in the skill's
`references/uniqueness.md`. Short version:

**A new build must differ from EVERY row below on at least 4 of the 6
dimensions.** Four against each row individually, not four on average across the
table. If a planned build fails, change the plan. Never edit a row to make room
for it.

The six dimensions are: **grammar**, **nav treatment**, **hero device**,
**act-sequence shape**, **close pattern**, **signature move**.

Dimension 6 is free, because a signature move is unique by definition. So the
gate really asks for three more out of the remaining five, and a build that
changes only grammar and world will fail it.

---

## The registry

| Build | Grammar | Nav treatment | Hero device | Act-sequence shape | Close pattern | Signature move | World | Port |
|---|---|---|---|---|---|---|---|---|
| stoa-plan-to-evidence | Plan-to-Evidence | Fixed compact wordmark + one GitHub CTA | Direct technical CAD plan with independent depth planes | Pin > flow > pin > pan > pin > flow (9.9vh) | Dark editorial close with repeated CTA | Schema-grounded name tags stay registered to CAD source markers through sheet separation and reassembly | Technical CAD drafting SVG with hatching, dimensions, and labeled provenance | Desktop + portrait |
| stoa-plan-to-evidence / CAD revision 2026-11-14 (current) | Plan-to-Evidence, revised in place | Scrolling wordmark and source-section anchor; GitHub CTA in opening and close | Detailed CAD plan on independently moving paper and registration planes | Pin/parallax > flow/reveal > pin/separate > pan/rotate > pin/reveal/reassemble > flow; 6 beats, 8.46 desktop / 8.59 portrait / 8.77 compact viewports | Ink-ground editorial close after the registered, annotated drawing | Five persistent CAD sheets separate with clear gaps, rotate with face-on coordinate-anchored names, and register into the source-tagged plan | Native architectural SVG; cut hatching, true door arcs, mullions, dimensions, axes, north, scale, title blocks; no external assets | 4500 |
| stoa-plan-to-evidence / centered hero follow-up (latest) | Plan-to-Evidence, revised in place | Scrolling wordmark and source-section anchor; opening and closing GitHub CTAs | Centered CAD sheet beneath a compact text row; viewport-bound sizing and top-aligned phone composition | Same six beats and scroll spans as CAD revision | Ink-ground editorial close after annotated blueprint | Persistent source-tagged separation, rotation, and registration, unchanged | Native architectural SVG, unchanged | 4500 |
| stoa-plan-to-evidence / enlarged blue blueprint | Plan-to-Evidence, revised in place | Scrolling wordmark and source-section anchor; project CTA only at close | Retained headline above enlarged central blueprint; opening description and button removed | Same six beats and scroll spans as CAD revision | Ink-ground editorial close after annotated blueprint | Persistent source-tagged separation, rotation, and registration; outlined callout leaders cross blue paper | Blue native CAD sheets with white drafting lines and labels; six color tokens | 4500 |

Rows are chronological; the enlarged-blue-blueprint entry supersedes earlier current/latest labels.
The first row is retained as the historical build record. The revision rows record
the user's requested revision of that same site; it does not claim a new site or
a 4-of-6 uniqueness pass against its own earlier revision. No other sites are
registered here.

---

## What is taken

Add a bullet here whenever a build claims something a later build should avoid
reusing: a grammar, a nav treatment, a close pattern, a signature move, an
act-count-and-length band. The shared columns are what the next build inherits
as a constraint, so writing them down is the whole point.

- Plan-to-Evidence grammar, fixed compact wordmark navigation, direct CAD plan hero, six-act pin/flow/pan sequence, dark editorial close, and named source-registration-through-reassembly signature are claimed by `stoa-plan-to-evidence`.
- The current revision retains the grammar and editorial close, changes navigation and hero composition, uses five distinct device families, and replaces placeholder sheets with one persistent, detailed CAD set. Its portrait composition uses a vertically separated perspective stack. Current evidence: `builds/stoa-plan-to-evidence/VERIFICATION.md` and `lab/cad-*-final/`.
- Centered-hero follow-up: complete CAD drawing centered beneath compact opening copy and constrained to fit the first viewport; evidence in `builds/stoa-plan-to-evidence/lab/hero-centered-*/`. The other five fingerprint dimensions are intentionally retained for this requested in-place correction.
- Enlarged-blue-blueprint revision: opening reduced to headline and larger drawing; blue paper and white outlines throughout. Evidence: `builds/stoa-plan-to-evidence/lab/blueprint-blue-*-final/`. This is another requested revision of the same site, not a new-site uniqueness claim.

---

## Appending a row

After shipping, add one line to the table and one bullet to **What is taken** if
the build claimed something new. Fill every column. Say what the build shares
with existing rows.

Rows are append-only. A build that has been superseded stays in the table,
because the space it occupies is still occupied.

---

## Worked example

The skill's author kept a registry of twelve builds across eight page grammars.
If you want to see what a filled-in table looks like, and which shapes tend to
collide, read `EXAMPLES.md` in the scroll-craft repository. Treat it as
illustration only: those rows are somebody else's builds and they do **not**
constrain yours.
