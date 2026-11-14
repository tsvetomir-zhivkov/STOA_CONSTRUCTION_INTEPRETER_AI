import fs from 'node:fs/promises';
import { createVersionedValidator } from '../src/schema/versions.js';
import { toCanonical } from '../src/schema/canonical.js';
import { writeJsonAtomic } from '../src/storage/jsonStore.js';
const [source, target] = process.argv.slice(2);
const document = JSON.parse(await fs.readFile(source, 'utf8'));
(await createVersionedValidator()).assertValid(document);
await writeJsonAtomic(target, toCanonical(document));
