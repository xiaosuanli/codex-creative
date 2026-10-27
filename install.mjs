import { cp, lstat, mkdir, readFile, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { codexHookCommand, isDirectorHook } from './xiaosuanli-director/scripts/lib/connect/codex-shell.mjs';

const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 19)) throw Error('需要 Node.js 22.19 或更高版本');
const home = resolve(process.env.CODEX_HOME || join(homedir(), '.codex'));
const destination = join(home, 'skills', 'xiaosuanli-director');
const hookFile = join(home, 'hooks.json');
try { await lstat(destination); throw Error('同名 skill 已存在，请先备份并移走旧目录'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
let config = {};
try { config = JSON.parse(await readFile(hookFile, 'utf8')); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
config.hooks ||= {};
const hookPath = join(destination, 'scripts', 'codex-hook.mjs');
const command = codexHookCommand(process.execPath, hookPath);
const commandWindows = codexHookCommand(process.execPath, hookPath, 'win32');
for (const event of ['UserPromptSubmit', 'PreToolUse']) {
  const groups = config.hooks[event] || [];
  config.hooks[event] = groups.map(group => ({ ...group, hooks: group.hooks.filter(hook => !isDirectorHook(hook)) })).filter(group => group.hooks.length);
  config.hooks[event].push({ ...(event === 'PreToolUse' ? { matcher: '^(Bash|exec_command)$' } : {}), hooks: [{ type: 'command', command, commandWindows, timeout: 65 }] });
}
await mkdir(join(home, 'skills'), { recursive: true });
await cp(fileURLToPath(new URL('./xiaosuanli-director', import.meta.url)), destination, { recursive: true, force: false, errorOnExist: true });
await writeFile(hookFile, JSON.stringify(config, null, 2) + '\n', { mode: 0o600 });
console.log('已安装小算狸导演 Skill。请在 Codex /hooks 中审阅并信任小算狸钩子，然后发送新消息。');
