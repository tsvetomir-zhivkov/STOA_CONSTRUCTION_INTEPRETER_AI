/**
 * Schema versions: which schema file validates which document, plus the relationship check for v0.3.
 * Everything that has to know "v0.2 or v0.3" goes through here.
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createValidator, describeErrors } from '../storage/jsonStore.js';
import { detectVersion, migrateV02toV03, validateRelationships } from './v03.js';
import { SCHEMA_PATH } from '../config.js';
import { SchemaValidationError } from '../util/errors.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SCHEMA_PATHS = Object.freeze({
  '0.2': path.resolve(HERE, '..', '..', 'schema', 'blueprint.schema.json'),
  '0.3': path.resolve(HERE, '..', '..', 'schema', 'building.schema.v0.3.json'),
});
export const SUPPORTED = Object.keys(SCHEMA_PATHS);
export const DEFAULT_OUTPUT_VERSION = '0.3';

export { detectVersion, migrateV02toV03, validateRelationships };

/**
 * One validator per version. validate(doc) picks the schema from doc.schema_version; v0.3 also runs the relationship check.
 * @param {{ schemaPaths?: Record<string,string> }} [o]
 */
export async function createVersionedValidator(o = {}) {
  const paths = { ...SCHEMA_PATHS, ...(o.schemaPaths ?? {}) };
  const canonical = await createValidator(SCHEMA_PATH);
  const byVersion = {};
  for (const [v, p] of Object.entries(paths)) byVersion[v] = await createValidator(p);
  /** @returns {{ valid:boolean, version:string|null, errors:any[], relationshipErrors:string[] }} */
  const validate = (doc) => {
    const version = detectVersion(doc);
    const isCanonical = !!doc?.building_id && Array.isArray(doc.drawings);
    const v = isCanonical ? canonical : byVersion[version];
    if (!v) return { valid: false, version, errors: [{ instancePath: '/schema_version', message: `unsupported schema_version ${JSON.stringify(doc?.schema_version)} (supported: ${SUPPORTED.join(', ')})`, params: {} }], relationshipErrors: [] };
    const r = v.validate(doc);
    const relationshipErrors = version === '0.3' && !isCanonical && r.valid ? validateRelationships(doc) : [];
    return { valid: r.valid && !relationshipErrors.length, version, errors: r.errors, relationshipErrors };
  };
  return {
    validate,
    /** @param {any} doc @param {string} [label] */
    assertValid(doc, label = 'document') {
      const r = validate(doc);
      if (r.valid) return doc;
      const msgs = [...describeErrors(r.errors), ...r.relationshipErrors];
      throw new SchemaValidationError(r.errors, `${label} does not conform to schema ${r.version ?? '?'}: ${msgs.slice(0, 3).join('; ')}${msgs.length > 3 ? ` (+${msgs.length - 3} more)` : ''}`);
    },
    describe: (r) => [...describeErrors(r.errors), ...r.relationshipErrors],
  };
}

/** Read any supported document as v0.3 (a v0.2 one is migrated in memory; the file is not touched). */
export function asV03(doc, opts) {
  return detectVersion(doc) === '0.3' ? doc : migrateV02toV03(doc, opts);
}
