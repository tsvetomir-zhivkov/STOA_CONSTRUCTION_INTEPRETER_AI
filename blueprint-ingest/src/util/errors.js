/** Error types used across the pipeline. */

export class PipelineError extends Error {
  /**
   * @param {string} message
   * @param {string} code machine-readable code
   * @param {object} [details]
   */
  constructor(message, code = 'PIPELINE_ERROR', details = {}) {
    super(message);
    this.name = this.constructor.name;
    this.code = code;
    this.details = details;
  }
}

/** The merged/created document does not satisfy the JSON Schema. */
export class SchemaValidationError extends PipelineError {
  /** @param {import('ajv').ErrorObject[]} errors */
  constructor(errors, message = 'Document does not conform to blueprint.schema.json') {
    super(message, 'SCHEMA_INVALID', { errors });
    this.errors = errors;
  }
}

/** The file type cannot be processed by any route (e.g. binary DWG). */
export class UnsupportedFormatError extends PipelineError {
  constructor(message, details) {
    super(message, 'UNSUPPORTED_FORMAT', details);
  }
}

/** Incoming geometry lives in a different pixel frame than the existing document. */
export class FrameMismatchError extends PipelineError {
  constructor(message, details) {
    super(message, 'FRAME_MISMATCH', details);
  }
}
