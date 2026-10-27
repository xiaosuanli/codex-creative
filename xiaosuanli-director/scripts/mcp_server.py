"""本地 stdio MCP；业务和授权实现位于同目录 director.py。"""
import concurrent.futures
import json
import re
import sys
import threading

from director import Client, Failure, TURN, context_key

PROTOCOLS = {'2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'}
CONTEXT = {'type': 'string', 'description': 'Codex 宿主注入的内部消息上下文，不需要填写。'}


def schema(properties=None, required=None):
    return {'type': 'object', 'properties': (properties or {}) | {'_codex_context': CONTEXT},
            'required': required or [], 'additionalProperties': False}


def string(description):
    return {'type': 'string', 'description': description}


TOOLS = [
    {'name': 'status', 'description': '读取小算狸账号连接状态。', 'inputSchema': schema()},
    {'name': 'auth_start', 'description': '开始账号授权，返回供用户打开的网页链接和状态。', 'inputSchema': schema()},
    {'name': 'auth_complete', 'description': '使用用户提供的一次性授权码完成账号连接。', 'inputSchema': schema({'code': string('用户提供的一次性授权码'), 'state': string('本次授权的状态')}, ['code', 'state'])},
    {'name': 'account', 'description': '查看当前已授权账号、会员与积分。', 'inputSchema': schema()},
    {'name': 'projects', 'description': '列出已授权工程，供用户选择。', 'inputSchema': schema()},
    {'name': 'chapters', 'description': '列出所选工程的章节。', 'inputSchema': schema({'projectId': string('所选工程 ID')}, ['projectId'])},
    {'name': 'open_chapter', 'description': '在当前对话打开所选章节；一个对话固定一个章节。', 'inputSchema': schema({'projectId': string('所选工程 ID'), 'chapterId': string('所选章节 ID')}, ['projectId', 'chapterId'])},
    {'name': 'context', 'description': '查看当前对话绑定的章节和网页编辑链接。', 'inputSchema': schema()},
    {'name': 'create_project', 'description': '按用户要求创建工程及空白第一章。', 'inputSchema': schema({'title': string('工程名称'), 'requestId': string('稳定幂等键')}, ['title', 'requestId'])},
    {'name': 'create_chapter', 'description': '在所选工程中创建空白章节。', 'inputSchema': schema({'projectId': string('工程 ID'), 'scriptId': string('章节目录返回的剧本 ID'), 'title': string('章节名称'), 'requestId': string('稳定幂等键'), 'expectedVersion': {'type': 'integer', 'description': '最新工程版本'}}, ['projectId', 'scriptId', 'title', 'requestId', 'expectedVersion'])},
    {'name': 'capabilities', 'description': '读取当前可用导演操作及每项操作的准确参数。', 'inputSchema': schema()},
    {'name': 'execute', 'description': '调用已授权的导演操作；先用 capabilities 确认操作名与参数，按用户要求处理分镜、素材、生成和导出。', 'inputSchema': schema({'operation': string('capabilities 返回的操作名'), 'arguments': {'type': 'object', 'description': '按对应操作参数定义填写'}}, ['operation', 'arguments'])},
    {'name': 'logout', 'description': '撤销当前小算狸渠道授权。', 'inputSchema': schema()},
]
BY_NAME = {tool['name']: tool for tool in TOOLS}
SCOPED = {'open_chapter', 'context', 'create_project', 'create_chapter', 'execute'}


def require_context(client, key):
    if not isinstance(key, str) or not re.fullmatch(r'codex-turn-[a-f0-9]{64}', key):
        raise Failure('CONTEXT_REQUIRED', '缺少 Codex 消息接线；请在 /hooks 信任小算狸钩子并发送新消息。')
    context = client.store.get(key)
    if not context or not all(isinstance(context.get(k), str) and TURN.fullmatch(context[k]) for k in ('conversationId', 'userTurnId')) or context_key(context['conversationId'], context['userTurnId']) != key:
        raise Failure('CONTEXT_INVALID', 'Codex 消息上下文无效，请发送新消息后重试。')
    return context


def call_tool(name, arguments):
    tool = BY_NAME.get(name)
    if not tool or not isinstance(arguments, dict):
        raise Failure('INVALID_ARGUMENTS', '工具或参数无效。')
    definition = tool['inputSchema']
    if set(arguments) - set(definition['properties']) or any(key not in arguments for key in definition['required']):
        raise Failure('INVALID_ARGUMENTS', '工具参数缺失或包含不支持的字段。')
    for key, value in arguments.items():
        expected = definition['properties'][key]['type']
        if expected == 'string' and not isinstance(value, str) or expected == 'object' and not isinstance(value, dict) or expected == 'integer' and (not isinstance(value, int) or isinstance(value, bool)):
            raise Failure('INVALID_ARGUMENTS', '工具参数类型不正确。')
    client = Client()
    context = require_context(client, arguments.get('_codex_context')) if name in SCOPED else None
    data = {key: value for key, value in arguments.items() if key != '_codex_context'}
    if name == 'status':
        return client.status()
    if name == 'auth_start':
        return client.auth_start()
    if name == 'auth_complete':
        return client.auth_complete(data)
    if name == 'account':
        return client.call('/api/subscriber/account', client.grant())
    if name == 'projects':
        return client.projects()
    if name == 'chapters':
        return client.chapters(data['projectId'])
    if name == 'capabilities':
        return client.capabilities()
    if name == 'logout':
        return client.logout()
    conversation, turn = context['conversationId'], context['userTurnId']
    if name == 'context':
        return client.context(conversation)
    if name == 'open_chapter':
        client.open_chapter(conversation, data['chapterId'], data['projectId'])
        client.begin_turn(conversation, turn)
        return client.context(conversation)
    if name == 'create_project':
        return client.create('project', data, conversation)
    if name == 'create_chapter':
        return client.create('chapter', data, conversation)
    if name == 'execute':
        client.begin_turn(conversation, turn)
        return client.execute(data['operation'], data['arguments'], conversation, turn)
    raise Failure('INVALID_OPERATION', '不支持的操作。')


def tool_result(name, arguments):
    try:
        value = call_tool(name, arguments)
        return {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
    except (Failure, ValueError) as error:
        detail = {'error': str(error)}
        if isinstance(error, Failure):
            detail['code'] = error.code
        return {'isError': True, 'content': [{'type': 'text', 'text': json.dumps(detail, ensure_ascii=False)}]}
    except Exception:
        return {'isError': True, 'content': [{'type': 'text', 'text': '小算狸工具暂时不可用，请检查连接状态后重试。'}]}


def dispatch(message):
    method = message.get('method')
    params = message.get('params') or {}
    if not isinstance(params, dict):
        raise ValueError('Invalid parameters')
    if method == 'initialize':
        requested = params.get('protocolVersion')
        return {'protocolVersion': requested if requested in PROTOCOLS else '2025-06-18',
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'xiaosuanli', 'version': '1.0.0'},
                'instructions': '使用小算狸导演 Skill 理解成片意图。先连接账号并打开用户选择的章节，再使用 capabilities 和 execute 操作；请求完整成片时继续到导出完成。'}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        return tool_result(params.get('name'), params.get('arguments', {}))
    raise LookupError('Method not found')


def serve():
    lock = threading.Lock()

    def reply(message):
        request_id = message.get('id')
        try:
            result = dispatch(message)
            response = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except LookupError:
            response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'Method not found'}}
        except Exception:
            response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': 'Invalid request'}}
        with lock:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(',', ':')) + '\n')
            sys.stdout.flush()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
                    raise ValueError()
                if 'id' in message:
                    executor.submit(reply, message)
            except (ValueError, TypeError):
                with lock:
                    sys.stdout.write('{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}\n')
                    sys.stdout.flush()


if __name__ == '__main__':
    serve()
