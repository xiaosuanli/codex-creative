const posixQuote = value => "'" + value.replaceAll("'", "'\\''") + "'";
const powershellQuote = value => "'" + value.replaceAll("'", "''") + "'";
const utf8 = '$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)';
const powershell = script => 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + Buffer.from(script, 'utf16le').toString('base64');

export function codexHookCommand(executable, script, platform = process.platform) {
  if (platform !== 'win32') return posixQuote(executable) + ' ' + posixQuote(script);
  // Forward the hook's JSON stdin explicitly; Windows PowerShell otherwise
  // need not pass its redirected input to a native child process.
  return powershell(utf8 + '\n[Console]::In.ReadToEnd() | & ' + powershellQuote(executable) + ' ' + powershellQuote(script) + '\nexit $LASTEXITCODE');
}

export function bindCodexCommand(command, key, platform = process.platform) {
  if (platform !== 'win32') return 'env XSL_DIRECTOR_CONTEXT_KEY=' + posixQuote(key) + ' /bin/sh -c ' + posixQuote(command);
  // A fresh PowerShell owns this call's environment and original command.
  // UTF-8 also keeps Chinese JSON intact through Windows PowerShell 5.1 pipes.
  return powershell("$ErrorActionPreference = 'Stop'\n" + utf8 + '\n$env:XSL_DIRECTOR_CONTEXT_KEY = ' + powershellQuote(key) + '\n& {\n' + command + '\n}\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }');
}

export function isDirectorHook(hook) {
  return [hook.command, hook.commandWindows].some(command => {
    if (typeof command !== 'string') return false;
    const encoded = /^powershell\.exe\s+-NoProfile\s+-NonInteractive\s+-EncodedCommand\s+([A-Za-z0-9+/=]+)$/.exec(command);
    const source = encoded ? Buffer.from(encoded[1], 'base64').toString('utf16le') : command;
    return source.replaceAll('\\', '/').includes('xiaosuanli-director/scripts/codex-hook.mjs');
  });
}
