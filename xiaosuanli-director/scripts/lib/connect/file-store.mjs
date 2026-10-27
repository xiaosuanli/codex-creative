import { mkdir, open, rename, rm, lstat, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';

// Private application state, never inside the skill's installation directory.
export function fileStore(root) {
  const path = key => {
    if (!/^[a-zA-Z0-9-]+$/.test(key)) throw Error('Invalid credential key');
    return join(root, key + '.json');
  };
  return {
    async get(key) {
      const file = path(key);
      let stat;
      try { stat = await lstat(file); } catch (error) { if (error.code === 'ENOENT') return; throw error; }
      if (!stat.isFile() || stat.isSymbolicLink() || process.platform !== 'win32' && (stat.mode & 0o077)) throw Error('Credential file must be private');
      return JSON.parse(await readFile(file, 'utf8'));
    },
    async set(key, value) {
      await mkdir(root, { recursive: true, mode: 0o700 });
      const file = path(key), temporary = file + '.' + randomBytes(12).toString('hex');
      const handle = await open(temporary, 'wx', 0o600);
      try { await handle.writeFile(JSON.stringify(value)); await handle.close(); await rename(temporary, file); }
      catch (error) { await handle.close(); await rm(temporary, { force: true }); throw error; }
    },
    async delete(key) { await rm(path(key), { force: true }); },
  };
}
