/**
 * JSON persistence + schema validation.
 *  - Ajv 2020-12 (the schema declares draft 2020-12); `ajv-formats` is used when installed, otherwise a built-in date-time check.
 *  - atomic writes: data goes to a unique temp file in the SAME directory, is fsynced, then renamed over the target
 *    (rename is atomic on one filesystem, so a crash never leaves a half-written index).
 *  - sub-schema helpers so the repair pass can send the model only the failing sub-object and its (self-contained) schema.
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

// ---- sub-schema helpers ----------------------------------------------------------------------------------------------

/** Resolve a local "#/a/b" pointer. @param {any} root @param {string} ref */
export function resolvePointer(root, ref) {
  if (!ref.startsWith('#')) throw new Error(`Only local $refs are supported: ${ref}`);
  return ref.slice(1).split('/').filter(Boolean).reduce((n, k) => n?.[decodeURIComponent(k.replace(/~1/g, '/').replace(/~0/g, '~'))], root);
}

/**
 * Self-contained schema for a node of the main schema, simplified for Gemini's `responseJsonSchema`:
 * $refs inlined, prefixItems/const/format/exclusiveMinimum/default/description/$id dropped or rewritten.
 *
 * @param {any} root full schema
 * @param {string} pointer e.g. '#/properties/rooms/items'
 */
export function toGeminiSchema(root, pointer) {
  const walk = (node, depth) => {
    if (depth > 12 || node == null || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map((n) => walk(n, depth + 1));
    if (node.$ref) return walk({ ...resolvePointer(root, node.$ref), ...Object.fromEntries(Object.entries(node).filter(([k]) => k !== '$ref')) }, depth + 1);
    if (node.allOf) {
      // flatten allOf (schema v0.2 entities are `allOf: [entity]` + own properties) into one object schema
      const parts = [Object.fromEntries(Object.entries(node).filter(([k]) => k !== 'allOf')), ...node.allOf.map((s) => (s.$ref ? resolvePointer(root, s.$ref) : s))];
      const flat = { ...parts[0], properties: {}, required: [] };
      for (const part of parts) {
        Object.assign(flat.properties, part.properties ?? {});
        flat.required.push(...(part.required ?? []));
        if (part.type && !flat.type) flat.type = part.type;
      }
      flat.required = [...new Set(flat.required)];
      return walk(flat, depth + 1);
    }
    const out = {};
    for (const [k, v] of Object.entries(node)) {
      if (['$id', '$schema', '$defs', 'description', 'default', 'format', 'exclusiveMinimum', 'pattern', 'title', 'unevaluatedProperties'].includes(k)) continue;
      if (k === 'const') { out.enum = [v]; continue; }
      if (k === 'prefixItems') { out.items = { type: 'number' }; continue; }
      if (k === 'properties') out.properties = Object.fromEntries(Object.entries(v).map(([pk, pv]) => [pk, walk(pv, depth + 1)]));
      else if (k === 'oneOf') out.anyOf = walk(v, depth + 1);
      else out[k] = walk(v, depth + 1);
    }
    return out;
  };
  return walk(resolvePointer(root, pointer), 0);
}

/**
 * Group Ajv errors by the smallest repairable unit (an element of a top-level array, or the top-level object).
 * @param {import('ajv').ErrorObject[]} errors
 * @returns {Map<string, import('ajv').ErrorObject[]>} key e.g. '/rooms/3' or '/drawing'
 */
export function groupErrorsByElement(errors) {
  const map = new Map();
  for (const e of errors) {
    const seg = e.instancePath.split('/').filter(Boolean);
    const key = seg.length >= 2 && /^\d+$/.test(seg[1]) ? `/${seg[0]}/${seg[1]}` : seg.length ? `/${seg[0]}` : '';
    (map.get(key) ?? map.set(key, []).get(key)).push(e);
  }
  return map;
}

/** Schema pointer for an element key like '/rooms/3' -> '#/properties/rooms/items'. @param {string} key */
export function pointerForElement(key) {
  const seg = key.split('/').filter(Boolean);
  if (!seg.length) return '#';
  return seg.length >= 2 ? `#/properties/${seg[0]}/items` : `#/properties/${seg[0]}`;
}

/** @param {any} doc @param {string} key */
export function getByKey(doc, key) {
  return key.split('/').filter(Boolean).reduce((n, k) => n?.[k], doc);
}

/** @param {any} doc @param {string} key @param {any} value */
export function setByKey(doc, key, value) {
  const seg = key.split('/').filter(Boolean);
  const last = seg.pop();
  const parent = seg.reduce((n, k) => n[k], doc);
  parent[last] = value;
}

/** Compact one-line description of Ajv errors for prompts / logs. @param {import('ajv').ErrorObject[]} errors */
export function describeErrors(errors) {
  return errors.map((e) => `${e.instancePath || '/'} ${e.message}${e.params?.allowedValues ? ` (${e.params.allowedValues.join('|')})` : ''}${e.params?.additionalProperty ? ` [${e.params.additionalProperty}]` : ''}`);
}
