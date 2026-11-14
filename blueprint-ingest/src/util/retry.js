/**
 * Exponential backoff with full jitter for rate limits (429) and transient server errors (5xx).
 */

/** @param {number} ms */
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Pull an HTTP-ish status out of the different error shapes the Google SDK / fetch / Node produce.
 * @param {any} err
 * @returns {number|null}
 */
export function errorStatus(err) {
  const s = err?.status ?? err?.statusCode ?? err?.code ?? err?.error?.code ?? err?.response?.status;
  if (typeof s === 'number') return s;
  if (typeof s === 'string' && /^\d{3}$/.test(s)) return Number(s);
  const m = /\b(4\d\d|5\d\d)\b/.exec(String(err?.message ?? ''));
  return m ? Number(m[1]) : null;
}

const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
const RETRYABLE_CODES = new Set(['ECONNRESET', 'ETIMEDOUT', 'ECONNREFUSED', 'EAI_AGAIN', 'UND_ERR_SOCKET', 'UND_ERR_CONNECT_TIMEOUT']);

/** @param {any} err */
export function isRetryable(err) {
  const st = errorStatus(err);
  if (st && RETRYABLE_STATUS.has(st)) return true;
  if (RETRYABLE_CODES.has(err?.code) || RETRYABLE_CODES.has(err?.cause?.code)) return true;
  const msg = String(err?.message ?? '').toLowerCase();
  return /resource_exhausted|unavailable|overloaded|rate limit|quota|deadline/.test(msg) && !/api key|permission/.test(msg);
}

/**
 * @template T
 * @param {() => Promise<T>} fn
 * @param {{ retries?: number, baseMs?: number, maxMs?: number, onRetry?: (info:{attempt:number,delayMs:number,error:any})=>void, random?: () => number, sleepFn?: (ms:number)=>Promise<void> }} [opts]
 * @returns {Promise<T>}
 */
export async function withRetry(fn, opts = {}) {
  const { retries = 5, baseMs = 800, maxMs = 30000, onRetry, random = Math.random, sleepFn = sleep } = opts;
  let attempt = 0;
  for (;;) {
    try {
      return await fn();
    } catch (error) {
      if (attempt >= retries || !isRetryable(error)) throw error;
      attempt += 1;
      const cap = Math.min(maxMs, baseMs * 2 ** (attempt - 1));
      // "full jitter": uniform in [cap/2, cap] keeps a floor so we never hammer the API
      const delayMs = Math.round(cap / 2 + random() * (cap / 2));
      onRetry?.({ attempt, delayMs, error });
      await sleepFn(delayMs);
    }
  }
}
