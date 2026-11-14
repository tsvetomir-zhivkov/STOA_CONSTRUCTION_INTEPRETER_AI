/** Tracks Gemini token usage (and an optional USD estimate) across calls in one pipeline run. */

export class UsageTracker {
  /** @param {{ pricePerMInput?: number|null, pricePerMOutput?: number|null }} [pricing] */
  constructor(pricing = {}) {
    this.pricing = pricing;
    this.calls = 0;
    this.inputTokens = 0;
    this.outputTokens = 0;
    /** @type {Record<string, {calls:number,inputTokens:number,outputTokens:number}>} */
    this.byModel = {};
    /** @type {Array<{label:string, model:string, inputTokens:number, outputTokens:number}>} */
    this.log = [];
  }

  /**
   * @param {string} model
   * @param {{ promptTokenCount?: number, candidatesTokenCount?: number, thoughtsTokenCount?: number }|undefined} meta
   * @param {string} [label]
   */
  add(model, meta, label = '') {
    const inp = meta?.promptTokenCount ?? 0;
    // thinking tokens are billed as output
    const out = (meta?.candidatesTokenCount ?? 0) + (meta?.thoughtsTokenCount ?? 0);
    this.calls += 1;
    this.inputTokens += inp;
    this.outputTokens += out;
    const m = (this.byModel[model] ??= { calls: 0, inputTokens: 0, outputTokens: 0 });
    m.calls += 1;
    m.inputTokens += inp;
    m.outputTokens += out;
    this.log.push({ label, model, inputTokens: inp, outputTokens: out });
  }

  /** @returns {number|null} USD estimate, or null when no prices are configured */
  get costUsd() {
    const { pricePerMInput: pi, pricePerMOutput: po } = this.pricing;
    if (pi == null || po == null) return null;
    return Number(((this.inputTokens * pi + this.outputTokens * po) / 1e6).toFixed(6));
  }

  snapshot() {
    return {
      calls: this.calls,
      inputTokens: this.inputTokens,
      outputTokens: this.outputTokens,
      costUsd: this.costUsd,
      byModel: this.byModel,
    };
  }
}
