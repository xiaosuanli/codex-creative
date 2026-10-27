import contextlib
import datetime
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / 'xiaosuanli-director/scripts/director.py'
spec = importlib.util.spec_from_file_location('director', source)
director = importlib.util.module_from_spec(spec)
spec.loader.exec_module(director)


class Handler(BaseHTTPRequestHandler):
    actual_turn = None
    captured = None

    def log_message(self, *args):
        pass

    def grant(self, project):
        return {'clientId': 'xiaosuanli-codex', 'channel': 'codex', 'accessToken': 'test-token', 'projectId': project, 'authorizationId': 'auth-1', 'scopes': ['project:select', 'project:write'], 'expiresAt': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()}

    def respond(self, data):
        raw = json.dumps({'code': 200, 'data': data}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.endswith('/projects'):
            self.respond({'projects': []})
        elif self.path.endswith('/capabilities'):
            self.respond({'projectId': 'project-1', 'chapterId': 'chapter-1', 'sessionId': 'auth-1', 'canWrite': True})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path.endswith('/authorizations/token'):
            if body.get('code') != 'once' or not body.get('codeVerifier'):
                self.send_error(400)
                return
            self.respond(self.grant(''))
        elif self.path.endswith('/conversations/open'):
            self.respond(self.grant('project-1') | {'chapterId': 'chapter-1', 'conversationId': 'chat-1'})
        elif self.path.endswith('/conversations/user-turn'):
            Handler.actual_turn = body['userTurnId']
            self.respond({})
        elif self.path.endswith('/tools/project_get'):
            Handler.captured = (body, self.headers.get('X-Director-User-Turn'))
            self.respond({'ok': True})
        else:
            self.send_error(404)


class DirectorTest(unittest.TestCase):
    def test_authorization_chapter_and_turn(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XSL_DIRECTOR_API_ORIGIN': address, 'XSL_DIRECTOR_SITE_ORIGIN': address, 'XSL_DIRECTOR_STATE_DIR': root}):
                client = director.Client()
                state = client.auth_start()['state']
                client.auth_complete({'code': 'once', 'state': state})
                client.open_chapter('chat-1', 'chapter-1', 'project-1')
                client.begin_turn('chat-1', 'turn-1')
                client.execute('project_get', {}, 'chat-1', 'turn-1')
                self.assertEqual(Handler.actual_turn, 'turn-1')
                self.assertEqual(Handler.captured, ({'projectId': 'project-1'}, 'turn-1'))
                with self.assertRaises(director.Failure):
                    client.execute('project_get', {'projectId': 'other'}, 'chat-1', 'turn-1')
                with self.assertRaises(director.Failure):
                    client.open_chapter('chat-1', 'chapter-2', 'project-1')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_hook_binds_original_message(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'XSL_DIRECTOR_STATE_DIR': root}):
            director.handle_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'chat-1', 'turn_id': 'turn-1'})
            call = {'hook_event_name': 'PreToolUse', 'session_id': 'chat-1', 'turn_id': 'turn-1', 'tool_name': 'exec_command', 'tool_input': {'cmd': '/python /some/xiaosuanli-director/scripts/director.py execute'}}
            output = director.handle_hook(call)['hookSpecificOutput']
            self.assertEqual(output['permissionDecision'], 'allow')
            self.assertIn(director.context_key('chat-1', 'turn-1'), output['updatedInput']['cmd'])
            call['turn_id'] = 'turn-2'
            self.assertEqual(director.handle_hook(call)['hookSpecificOutput']['permissionDecision'], 'deny')
            for file in Path(root).iterdir():
                self.assertNotIn('prompt', file.read_text())

    def test_setup_preserves_other_hooks_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'CODEX_HOME': root}):
            path = Path(root) / 'hooks.json'
            path.write_text(json.dumps({'hooks': {'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'echo existing'}]}]}}))
            self.assertTrue(director.setup()['changed'])
            # Installed paths include the skill name, as in an actual installation.
            with patch.object(director, '__file__', str(Path(root) / 'xiaosuanli-director/scripts/director.py')):
                self.assertTrue(director.setup()['changed'])
                self.assertFalse(director.setup()['changed'])
            hooks = json.loads(path.read_text())['hooks']
            self.assertEqual(hooks['UserPromptSubmit'][0]['hooks'][0]['command'], 'echo existing')
            self.assertEqual(len(hooks['UserPromptSubmit']), 2)
            self.assertEqual(len(hooks['PreToolUse']), 1)


if __name__ == '__main__':
    unittest.main()
