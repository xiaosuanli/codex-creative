import { readFile } from 'node:fs/promises';
import { main } from './lib/connect/cli.mjs';
const environment = JSON.parse(await readFile(new URL('../environment.json', import.meta.url), 'utf8'));
process.env.XSL_DIRECTOR_API_ORIGIN ||= environment.apiOrigin;
process.env.XSL_DIRECTOR_SITE_ORIGIN ||= environment.siteOrigin;
main().catch(error => { console.error(error.message); process.exitCode = 1; });
