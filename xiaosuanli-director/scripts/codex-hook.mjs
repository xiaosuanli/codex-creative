import { readFile } from 'node:fs/promises';
import { createClient } from './lib/connect/cli.mjs';
import { codexHook } from './lib/connect/codex-context.mjs';
const environment = JSON.parse(await readFile(new URL('../environment.json', import.meta.url), 'utf8'));
process.env.XSL_DIRECTOR_API_ORIGIN ||= environment.apiOrigin;
process.env.XSL_DIRECTOR_SITE_ORIGIN ||= environment.siteOrigin;
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
try {
  const result = await codexHook(JSON.parse(raw), createClient('codex'));
  console.log(JSON.stringify(result));
} catch {
  // No prompt, transcript, private state or upstream diagnostic in hook output.
  console.error('小算狸消息接线暂不可用，请检查连接状态后重试');
  process.exitCode = 2;
}
