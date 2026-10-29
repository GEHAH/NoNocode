"""Exercise the CLI, real SDK, SSE parsing and file tools against a local API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml


@pytest.fixture
def local_anthropic(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            memory_call = 'memory extraction assistant' in str(request.get('system', ''))
            tool_result = any(b.get('type') == 'tool_result'
                              for m in request['messages']
                              for b in m['content'] if isinstance(b, dict))
            tool = not memory_call and not tool_result
            block = ({'type': 'tool_use', 'id': 'read-audit', 'name': 'ReadFile', 'input': {}}
                     if tool else {'type': 'text', 'text': ''})
            delta = ({'type': 'input_json_delta', 'partial_json': json.dumps({'file_path': 'input.txt'})}
                     if tool else {'type': 'text_delta', 'text': 'NONE' if memory_call else 'CLI_OK'})
            events = [
                {'type': 'message_start', 'message': {
                    'id': 'audit', 'type': 'message', 'role': 'assistant', 'model': 'audit',
                    'content': [], 'stop_reason': None, 'stop_sequence': None,
                    'usage': {'input_tokens': 10, 'output_tokens': 0}}},
                {'type': 'content_block_start', 'index': 0, 'content_block': block},
                {'type': 'content_block_delta', 'index': 0, 'delta': delta},
                {'type': 'content_block_stop', 'index': 0},
                {'type': 'message_delta', 'delta': {
                    'stop_reason': 'tool_use' if tool else 'end_turn', 'stop_sequence': None},
                    'usage': {'output_tokens': 3}},
                {'type': 'message_stop'},
            ]
            body = ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def run_cli(tmp_path, args):
    isolated_home = tmp_path / 'test-home'
    isolated_home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env['NONOCODE_TEST_HOME'] = str(isolated_home)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    env['NO_PROXY'] = '127.0.0.1,localhost'
    bootstrap = ("import os,runpy; from pathlib import Path; "
                 "Path.home=classmethod(lambda cls: Path(os.environ['NONOCODE_TEST_HOME'])); "
                 "runpy.run_module('nonocode',run_name='__main__')")
    return subprocess.run([sys.executable, '-c', bootstrap, *args], cwd=tmp_path,
                          env=env, text=True, capture_output=True, timeout=30)


def test_cli_help(tmp_path):
    result = run_cli(tmp_path, ['--help'])
    assert result.returncode == 0
    assert 'NoNocode personal AI assistant' in result.stdout
    assert '--remote' in result.stdout


@pytest.mark.parametrize('output_format', ['text', 'stream-json'])
def test_cli_real_sdk_stream_and_file_tool(tmp_path, local_anthropic, output_format):
    base_url, requests = local_anthropic
    config_dir = tmp_path / '.nonocode'
    config_dir.mkdir()
    config = {'providers': [{'name': 'audit', 'protocol': 'anthropic', 'model': 'audit',
                              'base_url': base_url, 'api_key': 'local-test-only',
                              'context_window': 128000, 'thinking': False}],
              'mcp_servers': []}
    (config_dir / 'config.yaml').write_text(yaml.safe_dump(config))
    (tmp_path / 'input.txt').write_text('audit-input-value')
    result = run_cli(tmp_path, ['-p', 'Read input.txt', '--output-format', output_format])
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert any('audit-input-value' in json.dumps(r['messages']) for r in requests)
    if output_format == 'text':
        assert result.stdout == 'CLI_OK'
    else:
        events = [json.loads(line) for line in result.stdout.splitlines()]
        assert any(e['type'] == 'tool_result' and not e['is_error'] for e in events)
        assert events[-1]['type'] == 'result'
        assert events[-1]['result'] == 'CLI_OK'


def test_cli_offline_history_and_doctor_without_api_configuration(tmp_path):
    history = run_cli(tmp_path,['--history','中文'])
    assert history.returncode == 0,history.stderr
    assert json.loads(history.stdout) == []
    runs = run_cli(tmp_path,['--runs'])
    assert runs.returncode == 0 and json.loads(runs.stdout) == []
    doctor = run_cli(tmp_path,['--doctor'])
    assert doctor.returncode == 0,doctor.stderr
    result = json.loads(doctor.stdout)
    assert result['version'] == '0.6.0'
    assert 'configuration' in result
    version = run_cli(tmp_path,['--version'])
    assert version.returncode == 0 and version.stdout.strip() == 'NoNocode 0.6.0'


def test_cli_saves_history_and_task_for_later_offline_lookup(tmp_path, local_anthropic):
    base_url,_ = local_anthropic
    directory = tmp_path/'.nonocode'
    directory.mkdir()
    (directory/'config.yaml').write_text(yaml.safe_dump({'providers':[
        {'name':'audit','protocol':'anthropic','model':'audit','base_url':base_url,
         'api_key':'fake-local-only','context_window':128000,'thinking':False}]}))
    (tmp_path/'input.txt').write_text('persisted-cli-input')
    result = run_cli(tmp_path,['-p','Read input.txt','--output-format','stream-json'])
    assert result.returncode == 0,result.stderr
    final = [json.loads(line) for line in result.stdout.splitlines()][-1]
    assert final['run_id'] and final['session_id']
    history = run_cli(tmp_path,['--history','persisted-cli-input'])
    assert json.loads(history.stdout)[0]['session_id'] == final['session_id']
    runs = json.loads(run_cli(tmp_path,['--runs']).stdout)
    assert runs[0]['id'] == final['run_id'] and runs[0]['status'] == 'completed'
