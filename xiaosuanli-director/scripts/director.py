"""小算狸 Codex 对话客户端；只使用 Python 标准库。"""
import base64
import datetime
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OPS = set('''project_get node_create node_update resource_create resource_update resource_import resource_bind image_generate image_status image_inspect image_bind video_generate task_get task_cancel task_retry context_select chapter_prepare video_transition result_select import_estimate text_import novel_import episode_inspect episode_import import_get import_cancel import_resume import_select_chapters director_start director_retry director_resume director_get director_cancel generation_start export_start export_get image_models library_list library_manage canvas_edit media_edit video_configure media_preview generation_list queue_cancel generation_cancel_node import_steps import_budget import_retry_speech canvas_history canvas_restore story_reparse review_acknowledge'''.split())
ID = re.compile(r'^[a-zA-Z0-9-]{1,80}$')
TURN = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


class Store:
    def __init__(self):
        self.root = Path(os.environ.get('XSL_DIRECTOR_STATE_DIR', Path.home() / '.config/xiaosuanli-director/codex'))

    def path(self, key):
        if not re.fullmatch(r'[a-zA-Z0-9-]+', key):
            raise ValueError('无效的凭据标识')
        return self.root / (key + '.json')

    def get(self, key):
        path = self.path(key)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and info.st_mode & 0o077):
            raise ValueError('凭据文件必须是私密的普通文件')
        return json.loads(path.read_text(encoding='utf-8'))

    def set(self, key, value):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path(key)
        tmp = path.with_name(path.name + '.' + secrets.token_hex(12))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(value, out, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)


def origin(value):
    u = urllib.parse.urlsplit(value)
    if u.username or u.password or u.query or u.fragment or u.path not in ('', '/') or not u.hostname or (u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in ('localhost', '127.0.0.1', '::1'))):
        raise Failure('INVALID_ORIGIN', '服务地址无效')
    return urllib.parse.urlunsplit((u.scheme, u.netloc, '', '', ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(base, path, grant=None, body=None, method='GET', turn=None):
    headers = {'Accept': 'application/json'}
    token = grant.get('accessToken') if grant else None
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if turn:
        headers['X-Director-User-Turn'] = turn
    if body is not None:
        headers['Content-Type'] = 'application/json'
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        try:
            response = urllib.request.build_opener(NoRedirect()).open(req, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, raw = response.status, response.read()
    except (OSError, urllib.error.URLError):
        raise Failure('RESULT_UNKNOWN', '连接中断，写操作可能已受理，请查询原任务或使用原 requestId 恢复')
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        raise Failure('INVALID_RESPONSE', '服务响应不可读取，请查询原请求')
    if not 200 <= status < 300 or envelope.get('code') != 200:
        code = envelope.get('code', status)
        message = envelope.get('message') if isinstance(code, int) and 400 <= code < 500 else None
        message = message if isinstance(message, str) and message else '服务未完成请求，请查询原任务后重试'
        if token:
            message = message.replace(token, '[redacted]')
        raise Failure(code, re.sub(r'https?://\S+', '[service address]', message)[:500])
    return envelope.get('data')


def valid_until(value):
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() > time.time()
    except (AttributeError, TypeError, ValueError):
        return False


class Client:
    def __init__(self):
        self.store = Store()
        self.api = origin(os.environ.get('XSL_DIRECTOR_API_ORIGIN', 'https://api.xiaosuanli.com'))
        self.site = origin(os.environ.get('XSL_DIRECTOR_SITE_ORIGIN', 'https://www.xiaosuanli.com'))
        identity = json.dumps([self.api, self.site, 'xiaosuanli-codex'], separators=(',', ':'), ensure_ascii=False)
        self.key = 'director-' + digest(identity)

    def call(self, path, grant=None, body=None, method='GET', turn=None):
        return request(self.api, path, grant, body, method, turn)

    def validate(self, grant):
        if not isinstance(grant, dict) or grant.get('clientId') != 'xiaosuanli-codex' or grant.get('channel') != 'codex' or not isinstance(grant.get('accessToken'), str) or not grant['accessToken'] or not isinstance(grant.get('scopes'), list) or not valid_until(grant.get('expiresAt')):
            raise Failure('INVALID_GRANT', '授权身份或有效期不匹配，请重新连接')
        project = grant.get('projectId')
        if not isinstance(project, str) or not (ID.fullmatch(project) or project == '' and 'project:select' in grant['scopes'] and ID.fullmatch(grant.get('authorizationId', ''))):
            raise Failure('INVALID_GRANT', '授权工程不匹配，请重新连接')

    def grant(self):
        selected = self.store.get(self.key)
        if not selected or not isinstance(selected.get('key'), str) or not selected['key'].startswith(self.key + '-'):
            raise Failure('LOGIN_REQUIRED', '请先完成小算狸账号授权')
        grant = self.store.get(selected['key'])
        self.validate(grant)
        suffix = grant['projectId'] or 'account-' + grant['authorizationId']
        if grant.get('apiOrigin') != self.api or grant.get('siteOrigin') != self.site or selected['key'] != self.key + '-' + suffix:
            raise Failure('INVALID_GRANT', '授权服务或工程不匹配')
        return grant

    def auth_start(self):
        verifier, state = b64(secrets.token_bytes(32)), b64(secrets.token_bytes(24))
        expires = int(time.time() * 1000) + 300000
        self.store.set(self.key + '-pending-login', {'verifier': verifier, 'state': state, 'expiresAt': expires})
        query = urllib.parse.urlencode({'clientId': 'xiaosuanli-codex', 'codeChallenge': b64(hashlib.sha256(verifier.encode()).digest()), 'codeChallengeMethod': 'S256', 'state': state})
        return {'url': self.site + '/authorize/director?' + query, 'state': state, 'expiresAt': datetime.datetime.fromtimestamp(expires / 1000, datetime.timezone.utc).isoformat()}

    def status(self):
        try:
            grant = self.grant()
            return {key: grant[key] for key in ('clientId', 'channel', 'projectId', 'scopes', 'expiresAt')} | {'connected': True}
        except Failure as error:
            pending = self.store.get(self.key + '-pending-login') if error.code == 'LOGIN_REQUIRED' else None
            if pending and pending.get('expiresAt', 0) > time.time() * 1000:
                return {'connected': False, 'clientId': 'xiaosuanli-codex', 'channel': 'codex', 'reason': 'LOGIN_EXCHANGE_INTERRUPTED' if pending.get('exchangeInterrupted') else 'LOGIN_PENDING', 'state': pending['state']}
            return {'connected': False, 'clientId': 'xiaosuanli-codex', 'channel': 'codex', 'reason': error.code}

    def auth_complete(self, data):
        key = self.key + '-pending-login'
        pending = self.store.get(key)
        if not pending or pending.get('state') != data.get('state') or pending.get('expiresAt', 0) <= time.time() * 1000 or not isinstance(data.get('code'), str) or not data['code'].strip():
            raise Failure('INVALID_LOGIN', '授权已过期或不匹配，请重新发起')
        try:
            grant = self.call('/api/subscriber/authorizations/token', body={'clientId': 'xiaosuanli-codex', 'code': data['code'].strip(), 'codeVerifier': pending['verifier']}, method='POST')
            self.validate(grant)
        except Failure as error:
            if error.code in ('RESULT_UNKNOWN', 'INVALID_RESPONSE'):
                pending['exchangeInterrupted'] = True
                self.store.set(key, pending)
            else:
                self.store.delete(key)
            raise
        suffix = grant['projectId'] or 'account-' + grant['authorizationId']
        grant_key = self.key + '-' + suffix
        self.store.set(grant_key, grant | {'apiOrigin': self.api, 'siteOrigin': self.site})
        self.store.set(self.key, {'key': grant_key})
        self.store.delete(key)
        return self.status() | {'initialization': self.projects(), 'next': '让用户选择工程，再列出章节'}

    def projects(self):
        return self.call('/api/subscriber/automated-director/projects', self.grant())

    def chapters(self, project):
        grant = self.grant()
        project = project or grant['projectId']
        if not isinstance(project, str) or not ID.fullmatch(project) or grant['projectId'] and grant['projectId'] != project:
            raise Failure('PROJECT_NOT_AUTHORIZED', '请选择已授权工程')
        return self.call('/api/subscriber/automated-director/projects/' + project + '/chapters', grant)

    def conversation_key(self, conversation):
        if not isinstance(conversation, str) or not TURN.fullmatch(conversation):
            raise Failure('CONVERSATION_REQUIRED', '请在具体对话中打开章节')
        return self.key + '-conversation-' + digest(conversation)

    def conversation_grant(self, conversation):
        grant = self.store.get(self.conversation_key(conversation))
        if not grant:
            raise Failure('CHAPTER_NOT_OPEN', '请先在当前对话打开工程和章节')
        self.validate(grant)
        if grant.get('conversationId') != conversation or not grant.get('chapterId') or grant.get('apiOrigin') != self.api or grant.get('siteOrigin') != self.site:
            raise Failure('INVALID_GRANT', '对话的工程或章节绑定不匹配')
        return grant

    def editor(self, project):
        return self.site + '/apps/automated-director?' + urllib.parse.urlencode({'project': project})

    def capabilities(self):
        grant = self.grant()
        result = self.call('/api/subscriber/automated-director/capabilities', grant)
        if not isinstance(result, dict) or result.get('clientId') != grant['clientId'] or result.get('channel') != grant['channel'] or result.get('projectId') != grant['projectId'] or not isinstance(result.get('tools'), list):
            raise Failure('INVALID_CAPABILITIES', '服务能力与当前授权不匹配')
        result['tools'] = [tool for tool in result['tools'] if isinstance(tool, dict) and tool.get('operation') in OPS and isinstance(tool.get('inputSchema'), dict) and tool['inputSchema'].get('type') == 'object']
        return result

    def context(self, conversation):
        grant = self.conversation_grant(conversation)
        state = self.call('/api/subscriber/automated-director/capabilities', grant)
        if state.get('projectId') != grant['projectId'] or state.get('chapterId') != grant['chapterId'] or state.get('sessionId') != grant.get('authorizationId'):
            raise Failure('INVALID_CAPABILITIES', '对话状态与固定章节不匹配')
        return {'connected': True, 'clientId': grant['clientId'], 'channel': grant['channel'], 'projectId': grant['projectId'], 'chapterId': grant['chapterId'], 'conversationId': conversation, 'editorUrl': self.editor(grant['projectId']), 'canWrite': state.get('canWrite') is True, 'userTurnId': state.get('userTurnId')}

    def open_chapter(self, conversation, chapter, project):
        key = self.conversation_key(conversation)
        existing = self.store.get(key)
        if existing:
            if existing.get('chapterId') != chapter or project and existing.get('projectId') != project:
                raise Failure('CHAPTER_ALREADY_BOUND', '当前对话已固定到另一章节，请新建对话')
            return self.context(conversation)
        parent = self.grant()
        project = project or parent['projectId']
        grant = self.call('/api/subscriber/automated-director/conversations/open', parent, {'conversationId': conversation, 'chapterId': chapter, 'projectId': project}, 'POST')
        self.validate(grant)
        if grant.get('projectId') != project or grant.get('chapterId') != chapter or grant.get('conversationId') != conversation:
            raise Failure('INVALID_GRANT', '服务端对话绑定不匹配')
        self.store.set(key, grant | {'apiOrigin': self.api, 'siteOrigin': self.site})
        return self.context(conversation)

    def begin_turn(self, conversation, turn):
        if not isinstance(turn, str) or not TURN.fullmatch(turn):
            raise Failure('USER_TURN_REQUIRED', '缺少真实用户消息标识')
        try:
            grant = self.conversation_grant(conversation)
        except Failure as error:
            if error.code != 'CHAPTER_NOT_OPEN':
                raise
            grant = self.grant()
        return self.call('/api/subscriber/automated-director/conversations/user-turn', grant, {'userTurnId': turn, 'conversationId': conversation}, 'POST')

    def create(self, operation, data, conversation):
        if self.store.get(self.conversation_key(conversation)):
            raise Failure('CHAPTER_ALREADY_BOUND', '当前对话已固定章节，请新建对话后创建')
        allowed = {'title', 'requestId'} if operation == 'project' else {'projectId', 'scriptId', 'title', 'requestId', 'expectedVersion'}
        if set(data) - allowed:
            raise Failure('INVALID_ARGUMENTS', '创建参数无效')
        grant = self.grant()
        project = data.get('projectId', '')
        if operation == 'chapter' and (not isinstance(project, str) or not ID.fullmatch(project) or grant['projectId'] and grant['projectId'] != project):
            raise Failure('PROJECT_NOT_AUTHORIZED', '请选择已授权工程')
        suffix = '/projects' if operation == 'project' else '/projects/' + project + '/chapters'
        result = self.call('/api/subscriber/automated-director' + suffix, grant, data | {'conversationId': conversation}, 'POST')
        return result | {'editorUrl': self.editor(result.get('project', {}).get('id') or project)}

    def execute(self, operation, args, conversation, turn):
        if operation not in OPS or not isinstance(args, dict):
            raise Failure('INVALID_ARGUMENTS', '业务参数无效')
        grant = self.conversation_grant(conversation)
        if args.get('projectId', grant['projectId']) != grant['projectId'] or set(args) & {'userId', 'clientId', 'channel', 'conversationId', 'userTurnId', 'billing', 'token', 'url'}:
            raise Failure('INVALID_ARGUMENTS', '工程或身份参数无效')
        return self.call('/api/subscriber/automated-director/tools/' + operation, grant, args | {'projectId': grant['projectId']}, 'POST', turn)

    def logout(self):
        self.store.delete(self.key + '-pending-login')
        selected = self.store.get(self.key)
        if not selected:
            return {'connected': False}
        try:
            self.call('/api/subscriber/authorizations/current', self.grant(), method='DELETE')
        except Failure as error:
            if error.code not in (401, 'INVALID_GRANT', 'LOGIN_REQUIRED'):
                raise
        if isinstance(selected.get('key'), str) and selected['key'].startswith(self.key + '-'):
            self.store.delete(selected['key'])
        self.store.delete(self.key)
        return {'connected': False}


def context_key(conversation, turn):
    return 'codex-turn-' + digest(conversation + '/' + turn)


def powershell(script):
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode(script.encode('utf-16le')).decode()


def psquote(value):
    return "'" + value.replace("'", "''") + "'"


UTF8 = "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n$env:PYTHONIOENCODING = 'utf-8'"


def hook_command(windows=False):
    script = str(Path(__file__).resolve())
    if not windows:
        return shlex.quote(sys.executable) + ' ' + shlex.quote(script) + ' hook'
    return powershell(UTF8 + '\n[Console]::In.ReadToEnd() | & ' + psquote(sys.executable) + ' ' + psquote(script) + ' hook\nexit $LASTEXITCODE')


def bind_command(command, key, windows=False):
    if not windows:
        return 'env XSL_DIRECTOR_CONTEXT_KEY=' + shlex.quote(key) + ' /bin/sh -c ' + shlex.quote(command)
    return powershell("$ErrorActionPreference = 'Stop'\n" + UTF8 + '\n$env:XSL_DIRECTOR_CONTEXT_KEY = ' + psquote(key) + '\n& {\n' + command + '\n}\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }')


def director_hook(hook):
    for command in (hook.get('command'), hook.get('commandWindows')):
        if not isinstance(command, str):
            continue
        encoded = re.fullmatch(r'powershell\.exe\s+-NoProfile\s+-NonInteractive\s+-EncodedCommand\s+([A-Za-z0-9+/=]+)', command)
        if encoded:
            try:
                command = base64.b64decode(encoded.group(1)).decode('utf-16le')
            except (ValueError, UnicodeError):
                continue
        command = command.replace('\\', '/')
        if any('xiaosuanli-director/' + name in command for name in ('scripts/codex-hook.mjs', 'scripts/director.py', 'bin/xsl-director-')):
            return True
    return False


def setup():
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
    hook_file = home / 'hooks.json'
    try:
        config = json.loads(hook_file.read_text(encoding='utf-8'))
    except FileNotFoundError:
        config = {}
    before = json.dumps(config, ensure_ascii=False)
    hooks = config.setdefault('hooks', {})
    for event in ('UserPromptSubmit', 'PreToolUse'):
        groups = []
        for group in hooks.get(event, []):
            keep = [hook for hook in group['hooks'] if not director_hook(hook)]
            if keep:
                groups.append(group | {'hooks': keep})
        new = {'hooks': [{'type': 'command', 'command': hook_command(), 'commandWindows': hook_command(True), 'timeout': 65}]}
        if event == 'PreToolUse':
            new['matcher'] = '^(Bash|exec_command|mcp__xiaosuanli__.*)$'
        hooks[event] = groups + [new]
    changed = before != json.dumps(config, ensure_ascii=False)
    if changed:
        home.mkdir(parents=True, exist_ok=True)
        temporary = home / ('.xsl-hooks-' + secrets.token_hex(12))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(config, out, ensure_ascii=False, indent=2)
                out.write('\n')
            os.replace(temporary, hook_file)
        finally:
            temporary.unlink(missing_ok=True)
    return {'changed': changed, 'message': '请在 Codex /hooks 审阅并信任小算狸钩子，然后发送新消息。'}


def handle_hook(data):
    conversation = data.get('agent_id') or data.get('session_id')
    turn = data.get('turn_id')
    if not all(isinstance(v, str) and TURN.fullmatch(v) for v in (conversation, turn)):
        raise ValueError('缺少有效的消息标识')
    client = Client()
    key = context_key(conversation, turn)
    event = data.get('hook_event_name')
    if event == 'UserPromptSubmit':
        client.store.set(key, {'conversationId': conversation, 'userTurnId': turn})
        try:
            client.begin_turn(conversation, turn)
        except Failure as error:
            if error.code not in ('CHAPTER_NOT_OPEN', 'LOGIN_REQUIRED'):
                return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': '小算狸消息同步暂不可用；业务调用前需要恢复连接。'}}
        return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': '小算狸已记录本条用户消息；业务调用使用随包工具。'}}
    if event != 'PreToolUse':
        return {}
    tool_name = data.get('tool_name')
    if isinstance(tool_name, str) and tool_name.startswith('mcp__xiaosuanli__'):
        tool_input = data.get('tool_input')
        if not isinstance(tool_input, dict):
            raise ValueError('MCP 工具输入无效')
        stored = client.store.get(key)
        if not stored or stored.get('conversationId') != conversation or stored.get('userTurnId') != turn:
            return {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': 'deny', 'permissionDecisionReason': '请先信任小算狸消息钩子并发送新消息。'}}
        return {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': 'allow', 'updatedInput': tool_input | {'_codex_context': key}}}
    field = {'Bash': 'command', 'exec_command': 'cmd'}.get(data.get('tool_name'))
    tool_input = data.get('tool_input') or {}
    command = tool_input.get(field) if field else None
    if not isinstance(command, str) or 'xiaosuanli-director/scripts/director.py' not in command.replace('\\', '/'):
        return {}
    stored = client.store.get(key)
    if not stored or stored.get('conversationId') != conversation or stored.get('userTurnId') != turn:
        return {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': 'deny', 'permissionDecisionReason': '请先信任小算狸消息钩子并发送新消息。'}}
    return {'hookSpecificOutput': {'hookEventName': event, 'permissionDecision': 'allow', 'updatedInput': tool_input | {field: bind_command(command, key, os.name == 'nt')}}}


def main(command):
    if command == 'setup':
        return setup()
    if command == 'hook':
        return handle_hook(json.load(sys.stdin))
    client = Client()
    data = {}
    if command in {'auth-complete', 'chapters', 'open-chapter', 'create-project', 'create-chapter', 'execute'}:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('输入过长，请选择单章范围')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('输入必须为 JSON 对象')
    context = None
    if command in {'context', 'open-chapter', 'create-project', 'create-chapter', 'execute'}:
        key = os.environ.get('XSL_DIRECTOR_CONTEXT_KEY', '')
        if not re.fullmatch(r'codex-turn-[a-f0-9]{64}', key):
            raise ValueError('缺少 Codex 消息接线；请信任钩子并发送新消息')
        context = client.store.get(key)
        if not context or not all(isinstance(context.get(k), str) and TURN.fullmatch(context[k]) for k in ('conversationId', 'userTurnId')) or context_key(context['conversationId'], context['userTurnId']) != key:
            raise ValueError('Codex 消息上下文无效')
    if command == 'auth-start':
        return client.auth_start()
    if command == 'auth-complete':
        return client.auth_complete(data)
    if command == 'status':
        return client.status()
    if command == 'account':
        return client.call('/api/subscriber/account', client.grant())
    if command == 'projects':
        return client.projects()
    if command == 'chapters':
        return client.chapters(data.get('projectId'))
    if command == 'capabilities':
        return client.capabilities()
    if command == 'context':
        return client.context(context['conversationId'])
    if command == 'open-chapter':
        client.open_chapter(context['conversationId'], data.get('chapterId'), data.get('projectId'))
        client.begin_turn(context['conversationId'], context['userTurnId'])
        return client.context(context['conversationId'])
    if command in {'create-project', 'create-chapter'}:
        return client.create(command.removeprefix('create-'), data, context['conversationId'])
    if command == 'execute':
        if set(data) - {'operation', 'arguments'}:
            raise ValueError('业务请求不能覆盖宿主身份')
        client.begin_turn(context['conversationId'], context['userTurnId'])
        return client.execute(data.get('operation'), data.get('arguments'), context['conversationId'], context['userTurnId'])
    if command == 'logout':
        return client.logout()
    raise ValueError('不支持的命令')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('请指定命令')
        print(json.dumps(main(sys.argv[1]), ensure_ascii=False))
    except Exception as error:
        print('小算狸消息接线暂不可用，请检查连接状态后重试' if sys.argv[1:] == ['hook'] else str(error), file=sys.stderr)
        sys.exit(2 if sys.argv[1:] == ['hook'] else 1)
