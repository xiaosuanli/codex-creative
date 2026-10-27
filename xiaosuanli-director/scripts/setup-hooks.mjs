import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { codexHookCommand, isDirectorHook } from './lib/connect/codex-shell.mjs';

export async function setupHooks(skillDirectory, home = resolve(process.env.CODEX_HOME || join(homedir(), '.codex'))) {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 22 || (major === 22 && minor < 19)) throw Error('需要 Node.js 22.19 或更高版本');
  const hookFile = join(home, 'hooks.json');
  let config = {};
  try { config = JSON.parse(await readFile(hookFile, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const before = JSON.stringify(config);
  config.hooks ||= {};
  const hookPath = join(skillDirectory, 'scripts', 'codex-hook.mjs');
  const command = codexHookCommand(process.execPath, hookPath);
  const commandWindows = codexHookCommand(process.execPath, hookPath, 'win32');
  for (const event of ['UserPromptSubmit', 'PreToolUse']) {
    const groups = config.hooks[event] || [];
    config.hooks[event] = groups.map(group => ({ ...group, hooks: group.hooks.filter(hook => !isDirectorHook(hook)) })).filter(group => group.hooks.length);
    config.hooks[event].push({ ...(event === 'PreToolUse' ? { matcher: '^(Bash|exec_command)$' } : {}), hooks: [{ type: 'command', command, commandWindows, timeout: 65 }] });
  }
  const changed = before !== JSON.stringify(config);
  if (changed) {
    await mkdir(home, { recursive: true });
    await writeFile(hookFile, JSON.stringify(config, null, 2) + '\n', { mode: 0o600 });
  }
  console.log(changed ? '已配置小算狸钩子。请在 Codex /hooks 审阅并信任，然后发送新消息。' : '小算狸钩子已配置；如尚未信任，请在 Codex /hooks 审阅并信任。');
  return { changed };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await setupHooks(dirname(dirname(fileURLToPath(import.meta.url))));
}
