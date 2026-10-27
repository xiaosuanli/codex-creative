import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_director import Handler

scripts = Path(__file__).resolve().parents[1] / 'xiaosuanli-director/scripts'
sys.path.insert(0, str(scripts))
import director
import mcp_server


class MCPTest(unittest.TestCase):
    def test_stdio_discovery_and_missing_context(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'execute', 'arguments': {'operation': 'project_get', 'arguments': {}}}},
        ]
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, str(scripts / 'mcp_server.py')], input=''.join(json.dumps(x) + '\n' for x in requests), text=True, capture_output=True, timeout=10, env=os.environ | {'XSL_DIRECTOR_STATE_DIR': root})
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = {item['id']: item for item in map(json.loads, result.stdout.splitlines())}
        self.assertEqual(responses[1]['result']['protocolVersion'], '2025-06-18')
        self.assertIn('execute', {tool['name'] for tool in responses[2]['result']['tools']})
        self.assertTrue(responses[3]['result']['isError'])
        self.assertEqual(json.loads(responses[3]['result']['content'][0]['text'])['code'], 'CONTEXT_REQUIRED')

    def test_hook_overrides_context_and_mcp_uses_original_turn(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XSL_DIRECTOR_API_ORIGIN': address, 'XSL_DIRECTOR_SITE_ORIGIN': address, 'XSL_DIRECTOR_STATE_DIR': root}):
                state = mcp_server.call_tool('auth_start', {})['state']
                mcp_server.call_tool('auth_complete', {'code': 'once', 'state': state})
                director.handle_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'chat-1', 'turn_id': 'turn-1'})

                def intercepted(name, args, turn='turn-1'):
                    event = {'hook_event_name': 'PreToolUse', 'session_id': 'chat-1', 'turn_id': turn, 'tool_name': 'mcp__xiaosuanli__' + name, 'tool_input': args}
                    return director.handle_hook(event)['hookSpecificOutput']

                opening = intercepted('open_chapter', {'projectId': 'project-1', 'chapterId': 'chapter-1', '_codex_context': 'invented'})
                self.assertEqual(opening['permissionDecision'], 'allow')
                self.assertNotEqual(opening['updatedInput']['_codex_context'], 'invented')
                mcp_server.call_tool('open_chapter', opening['updatedInput'])
                invocation = intercepted('execute', {'operation': 'project_get', 'arguments': {}})
                mcp_server.call_tool('execute', invocation['updatedInput'])
                self.assertEqual(Handler.captured, ({'projectId': 'project-1'}, 'turn-1'))
                self.assertEqual(intercepted('execute', {'operation': 'project_get', 'arguments': {}}, 'turn-2')['permissionDecision'], 'deny')
                with self.assertRaises(director.Failure):
                    mcp_server.call_tool('execute', {'operation': 'project_get', 'arguments': {}})
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
