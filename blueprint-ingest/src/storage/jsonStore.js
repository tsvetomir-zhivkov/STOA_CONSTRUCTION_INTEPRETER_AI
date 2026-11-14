/**
 * JSON persistence + schema validation.
 *  - Ajv 2020-12 (the schema declares draft 2020-12); `ajv-formats` is used when installed, otherwise a built-in date-time check.
 *  - atomic writes: data goes to a unique temp file in the SAME directory, is fsynced, then renamed over the target
 *    (rename is atomic on one filesystem, so a crash never leaves a half-written index).
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import Ajv2020 from 'ajv/dist/2020.js';
import { SchemaValidationError } from '../util/errors.js';
import { referenceErrors } from './building.js';
import { SCHEMA_PATH } from '../config.js';

let writeValidator;

const DATE_TIME = /^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$/;

/** @typedef {{ valid:boolean, errors: import('ajv').ErrorObject[] }} ValidationResult */

/**
 * @param {string} schemaPath
 * @returns {Promise<{ schema:any, validate:(doc:any)=>ValidationResult, assertValid:(doc:any)=>void }>}
 */
export async function createValidator(schemaPath) {
  const schema = JSON.parse(await fs.readFile(schemaPath, 'utf8'));
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  try {
    const mod = await import('ajv-formats');
    (mod.default ?? mod)(ajv);
  } catch {
    ajv.addFormat('date-time', DATE_TIME);
  }
  const fn = ajv.compile(schema);
  const validate = (doc) => {
    const valid = fn(doc);
    const errors = valid && doc.schema_version === '0.3'
      ? referenceErrors(doc).map(message => ({ instancePath: '', keyword: 'reference', message, params: {} }))
      : valid ? [] : [...(fn.errors ?? [])];
    return { valid: !!valid && errors.length === 0, errors };
  };
  return {
    schema,
    validate,
    assertValid(doc) {
      const r = validate(doc);
      if (!r.valid) throw new SchemaValidationError(r.errors);
    },
  };
}

/** @param {string} filePath @returns {Promise<any|null>} parsed JSON, or null when the file does not exist */
export async function readJsonIfExists(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
}

/**
 * Atomic write: <file>.<rand>.tmp -> fsync -> rename.
 * @param {string} filePath @param {any} data
 */
export async function writeJsonAtomic(filePath, data) {
  writeValidator ??= createValidator(SCHEMA_PATH);
  (await writeValidator).assertValid(data);
  const dir = path.dirname(filePath);
  await fs.mkdir(dir, { recursive: true });
  const tmp = path.join(dir, `.${path.basename(filePath)}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`);
  const fh = await fs.open(tmp, 'w');
  try {
    await fh.writeFile(`${JSON.stringify(data, null, 2)}\n`, 'utf8');
    await fh.sync();
  } finally {
    await fh.close();
  }
  try {
    await fs.rename(tmp, filePath);
  } catch (err) {
    await fs.rm(tmp, { force: true });
    throw err;
  }
}

/** Compact one-line description of Ajv errors for error messages / logs. @param {import('ajv').ErrorObject[]} errors */
export function describeErrors(errors) {
  return errors.map((e) => `${e.instancePath || '/'} ${e.message}${e.params?.allowedValues ? ` (${e.params.allowedValues.join('|')})` : ''}${e.params?.additionalProperty ? ` [${e.params.additionalProperty}]` : ''}`);
}
