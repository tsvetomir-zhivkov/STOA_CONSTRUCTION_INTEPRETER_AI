/**
 * Thin wrapper around the official `@google/genai` SDK.
 *
 *  - structured JSON output (`responseMimeType: application/json` + `responseJsonSchema`)
 *  - model tiers: primary / heavy / light, each with a fallback chain (a 404 "model not found" moves on to the next candidate and the
 *    working model is remembered for the rest of the run, so a wrong model id costs one failed call, not the whole pipeline)
 *  - retry with exponential backoff + jitter for 429/5xx, one re-ask for unparsable JSON
 *  - token accounting through UsageTracker
 *  - Files API helpers (upload / delete)
 *
 * The SDK is imported lazily; tests inject a fake via `deps.sdk` / `deps.ai`.
 */

import { withRetry, errorStatus } from '../util/retry.js';
import { PipelineError } from '../util/errors.js';
import { silentLogger } from '../util/logger.js';

/**
 * @typedef {Object} GenerateJsonArgs
 * @property {'primary'|'heavy'|'light'} [tier]
 * @property {string} label short name used in usage logs
 * @property {string} prompt
 * @property {any} schema JSON schema for the response
 * @property {string} [system]
 * @property {{ uri:string, mimeType:string }} [file] Files API reference to attach
 * @property {number} [temperature]
 */

/**
 * @param {{ config: import('../config.js').PipelineConfig, usage: import('../util/usage.js').UsageTracker, logger?: any, deps?: { ai?: any, sdk?: any } }} opts
 */
export function createGeminiClient({ config, usage, logger = silentLogger, deps = {} }) {
  const apiKey = config.gemini.apiKey;
  const isConfigured = !!(deps.ai || apiKey);
  /** @type {any} */
  let aiPromise = null;
  const getAi = () => {
    aiPromise ??= deps.ai
      ? Promise.resolve(deps.ai)
      : (deps.sdk ? Promise.resolve(deps.sdk) : import('@google/genai')).then((m) => new m.GoogleGenAI({ apiKey }));
    return aiPromise;
  };

  /** tier -> index of the candidate that last worked */
  const sticky = new Map();
  const candidates = (tier) => {
    const m = config.gemini.models;
    const first = tier === 'heavy' ? m.heavy : tier === 'light' ? m.light : m.primary;
    return [...new Set([first, ...(m.fallbacks ?? [])])];
  };

  const isModelMissing = (err) => {
    const st = errorStatus(err);
    const msg = String(err?.message ?? '').toLowerCase();
    return st === 404 || /not[_ ]found|is not supported|unknown model|invalid model/.test(msg);
  };

  /**
   * @param {GenerateJsonArgs} args
   * @returns {Promise<{ json:any, model:string, usageMetadata:any }>}
   */
  async function generateJson(args) {
    if (!isConfigured) throw new PipelineError('GEMINI_API_KEY is not set', 'GEMINI_NOT_CONFIGURED');
    const tier = args.tier ?? 'primary';
    const list = candidates(tier);
    let i = sticky.get(tier) ?? 0;
    const ai = await getAi();
    const parts = [{ text: args.prompt }];
    if (args.file) parts.push({ fileData: { fileUri: args.file.uri, mimeType: args.file.mimeType } });

    let lastErr;
    for (; i < list.length; i++) {
      const model = list[i];
      for (let ask = 0; ask < 2; ask++) { // second ask only when the JSON came back unparsable
        try {
          const res = await withRetry(
            () => ai.models.generateContent({
              model,
              contents: [{ role: 'user', parts }],
              config: {
                responseMimeType: 'application/json',
                responseJsonSchema: args.schema,
                temperature: args.temperature ?? 0,
                ...(args.system ? { systemInstruction: args.system } : {}),
              },
            }),
            { ...config.gemini.retry, onRetry: ({ attempt, delayMs, error }) => logger.warn(`gemini ${model} retry ${attempt} in ${delayMs}ms: ${error?.message ?? error}`) },
          );
          usage.add(model, res.usageMetadata, args.label);
          const text = typeof res.text === 'function' ? res.text() : res.text;
          try {
            const json = JSON.parse(stripFence(text ?? ''));
            sticky.set(tier, i);
            return { json, model, usageMetadata: res.usageMetadata };
          } catch (parseErr) {
            lastErr = new PipelineError(`Gemini returned invalid JSON (${parseErr.message})`, 'GEMINI_BAD_JSON');
            logger.warn(`gemini ${model}: unparsable JSON for ${args.label}${ask === 0 ? ', asking again' : ''}`);
          }
        } catch (err) {
          lastErr = err;
          if (isModelMissing(err) && i < list.length - 1) { logger.warn(`gemini model "${model}" unavailable (${err.message}); trying "${list[i + 1]}"`); break; }
          throw err;
        }
      }
      if (lastErr?.code === 'GEMINI_BAD_JSON') throw lastErr;
    }
    throw lastErr ?? new PipelineError('No Gemini model could be used', 'GEMINI_NO_MODEL');
  }

  /** @param {string} filePath @param {string} mimeType */
  async function uploadFile(filePath, mimeType) {
    const ai = await getAi();
    let f = await ai.files.upload({ file: filePath, config: { mimeType } });
    // large files are processed asynchronously; wait until ACTIVE
    for (let n = 0; n < 30 && f.state && String(f.state) === 'PROCESSING'; n++) {
      await new Promise((r) => setTimeout(r, 1000));
      f = await ai.files.get({ name: f.name });
    }
    return { name: f.name, uri: f.uri, mimeType: f.mimeType ?? mimeType };
  }

  /** @param {string} name */
  async function deleteFile(name) {
    const ai = await getAi();
    await ai.files.delete({ name });
  }

  return { isConfigured, generateJson, uploadFile, deleteFile, usage };
}

/** Models sometimes wrap JSON in ```json fences despite the mime type. @param {string} t */
function stripFence(t) {
  const m = /^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$/i.exec(t);
  return m ? m[1] : t;
}
