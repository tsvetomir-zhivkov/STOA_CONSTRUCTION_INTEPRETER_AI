import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const files=fs.readdirSync('.').filter(f=>f.endsWith('.js'));
for(const file of files){const result=spawnSync(process.execPath,['--check',file],{stdio:'inherit'});if(result.status)process.exit(result.status);}
const html=fs.readFileSync('index.html','utf8');
for(const match of html.matchAll(/<script type="module">([\s\S]*?)<\/script>/g)){
  const result=spawnSync(process.execPath,['--input-type=module','--check'],{input:match[1],encoding:'utf8'});
  if(result.status){console.error(result.stderr);process.exit(result.status);}
}
console.log(`Syntax checked ${files.length} modules and the inline controller.`);
