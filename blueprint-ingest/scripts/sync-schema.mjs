/**
 * Copies the v0.3 schema and the IFC discipline table to the Python translator (translator/schema/), so both sides
 * read the same files. `--check` exits 1 when the copies differ (used by the tests).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DISCIPLINE } from '../src/schema/ifcClasses.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const schemaSrc = path.join(ROOT, 'blueprint-ingest', 'schema', 'building.schema.v0.3.json');
const targets = {
  [path.join(ROOT, 'translator', 'schema', 'building.schema.v0.3.json')]: fs.readFileSync(schemaSrc, 'utf8'),
  [path.join(ROOT, 'translator', 'schema', 'ifc_disciplines.json')]: `${JSON.stringify(Object.fromEntries([...DISCIPLINE].sort()), null, 1)}\n`,
};
const check = process.argv.includes('--check');
let stale = 0;
for (const [p, content] of Object.entries(targets)) {
  const cur = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
  if (cur === content) continue;
  stale++;
  if (check) process.stderr.write(`out of date: ${path.relative(ROOT, p)} (run node blueprint-ingest/scripts/sync-schema.mjs)\n`);
  else { fs.writeFileSync(p, content); process.stdout.write(`wrote ${path.relative(ROOT, p)}\n`); }
}
process.exitCode = check && stale ? 1 : 0;
