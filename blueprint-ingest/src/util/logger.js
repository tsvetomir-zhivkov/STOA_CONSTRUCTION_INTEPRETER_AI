/** Minimal leveled logger that writes to stderr so stdout stays clean for CLI JSON output. */

const LEVELS = { silent: 0, error: 1, warn: 2, info: 3, debug: 4 };

/**
 * @param {string} [level]
 * @returns {{ error: Function, warn: Function, info: Function, debug: Function }}
 */
export function createLogger(level = process.env.LOG_LEVEL || 'info') {
  const max = LEVELS[level] ?? LEVELS.info;
  const emit = (lvl, args) => {
    if (LEVELS[lvl] <= max) console.error(`[${lvl}]`, ...args);
  };
  return {
    error: (...a) => emit('error', a),
    warn: (...a) => emit('warn', a),
    info: (...a) => emit('info', a),
    debug: (...a) => emit('debug', a),
  };
}

export const silentLogger = { error() {}, warn() {}, info() {}, debug() {} };
