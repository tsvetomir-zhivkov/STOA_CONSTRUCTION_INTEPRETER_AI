/**
 * Confidence policy (translator 0.2.1). ONE rule set, written here and used by the DXF extractor:
 *
 *   0.9  class read from the file (layer or block name matched) AND geometry measured
 *   0.7  geometry measured, class inferred from shape or context (e.g. treads inside a room named "Treppenhaus")
 *   0.4  any dimension assumed (e.g. a single wall line given a 0.2 m thickness)
 *
 * Every assumption also adds a reason to the element's `flags` (see FLAGS), so a low number always comes with a reason.
 *
 * The policy values are CAPS, never raises: an element keeps the lower of its previous confidence and the policy value
 * (`policyConfidence(evidence, prior)`). A number only goes up when the evidence behind it changed, e.g. a wall that
 * used to be an assumed 0.2 m line (0.4) and is now measured by line pairing (0.9). IFC, SVG and PDF keep their own
 * values (they are already at or below their caps, except IFC 0.95 which is deliberately left alone).
 *
 * A human correction (corrections.json) sets confidence 1.0 and provenance.extractor "human".
 */

export const POLICY = Object.freeze({ readAndMeasured: 0.9, inferredAndMeasured: 0.7, assumed: 0.4, human: 1.0 });

export const FLAGS = Object.freeze({
  assumedThickness: 'assumed_thickness',
  assumedHeight: 'assumed_height',
  inferredFromRoom: 'inferred_from_room',
  inferredFromContents: 'inferred_from_contents',
});

/**
 * @param {{ classRead: boolean, measured: boolean, assumed?: boolean }} evidence
 * @param {number} [prior] the confidence this element had before the policy (cap: never exceed it)
 */
export function policyConfidence(evidence, prior = 1) {
  const v = evidence.assumed || !evidence.measured ? POLICY.assumed : evidence.classRead ? POLICY.readAndMeasured : POLICY.inferredAndMeasured;
  return Math.min(v, prior);
}
