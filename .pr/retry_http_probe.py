"""Real HTTP provider -> optional LiteLLM proxy -> locked SDK reproduction."""
import argparse
import asyncio
import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def client(args):
    from pydantic import SecretStr
    from openhands.sdk import LLM
    from openhands.sdk.llm import Message, TextContent
    import openhands.sdk.llm.utils.retry_mixin as retry_module

    events = []
    def listener(attempt, maximum, error):
        sources = [getattr(error, 'headers', None), getattr(error, 'litellm_response_headers', None), getattr(getattr(error, 'response', None), 'headers', None)]
        hints = {k: v for source in sources if source for k, v in source.items() if 'retry-after' in k.lower()}
        events.append({'attempt': attempt, 'maximum': maximum, 'hints': hints})
    llm = LLM(model='openai/retry-probe', base_url=args.url, api_key=SecretStr('local-test'), num_retries=3, usage_id='retry-proof', retry_listener=listener)
    messages = [Message(role='system', content=[TextContent(text='Reply briefly.')]), Message(role='user', content=[TextContent(text='Reply OK.')])]
    start = time.monotonic()
    response = asyncio.run(llm.acompletion(messages=messages)) if args.asynchronous else llm.completion(messages=messages)
    Path(args.result).write_text(json.dumps({'elapsed': round(time.monotonic()-start, 3), 'retry_module': retry_module.__file__, 'events': events, 'response': response.message.model_dump(mode='json')}, indent=2))


def probe(args):
    result_dir = Path(args.out).resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    calls = []
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request['messages'][0]['role'] == 'system'
            if args.provider_route == 'azure':
                assert self.path == '/openai/deployments/retry-probe/chat/completions?api-version=2024-10-21', self.path
                assert self.headers.get('api-key') == 'local-test'
            paths.append(self.path)
            now = time.monotonic()
            calls.append(now)
            remaining = 12 - (now - calls[0])
            limited = remaining > 0
            body = {'error': {'message': 'Temporary request rate limit', 'type': 'rate_limit_error', 'code': 'rate_limit_exceeded'}} if limited else {'id': 'chat-retry', 'object': 'chat.completion', 'created': 1, 'model': 'retry-probe', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            raw = json.dumps(body).encode()
            self.send_response(429 if limited else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            if limited:
                self.send_header('Retry-After', str(math.ceil(remaining)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix='oh148-proxy-') as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                proxy_port = sock.getsockname()[1]
            config = Path(tmp)/'config.yaml'
            upstream_base = f'http://127.0.0.1:{provider.server_port}'
            if args.provider_route == 'openai':
                upstream_base += '/v1'
            azure_version = '      api_version: "2024-10-21"\n' if args.provider_route == 'azure' else ''
            config.write_text(f'''model_list:
  - model_name: retry-probe
    litellm_params:
      model: {args.provider_route}/retry-probe
      api_base: {upstream_base}
      api_key: local-test
{azure_version}litellm_settings:
  num_retries: 3
router_settings:
  retry_policy:
    RateLimitErrorRetries: 0
  disable_cooldowns: true
''')
            with (result_dir/'proxy.log').open('w') as log:
                proxy = subprocess.Popen(['uv','run','--no-project','--with',f'litellm[proxy]=={args.proxy_version}','litellm','--config',str(config),'--host','127.0.0.1','--port',str(proxy_port),'--telemetry','False'],cwd=tmp,stdout=log,stderr=log,start_new_session=True)
                try:
                    for _ in range(180):
                        if proxy.poll() is not None:
                            raise RuntimeError('Proxy exited; inspect proxy.log')
                        try:
                            urllib.request.urlopen(f'http://127.0.0.1:{proxy_port}/health/liveliness',timeout=1).close()
                            break
                        except Exception:
                            time.sleep(0.5)
                    else:
                        raise RuntimeError('Proxy startup timeout')
                    cases = [('baseline', args.baseline, False), ('candidate', args.candidate, False), ('candidate-async', args.candidate, True)]
                    if args.provider_route == 'azure':
                        cases.insert(1, ('baseline-async', args.baseline, True))
                    for mode, checkout, asynchronous in cases:
                        if args.candidate_only and mode.startswith('baseline'):
                            continue
                        routes = [('proxy', proxy_port)] if args.provider_route == 'azure' else [('direct', provider.server_port), ('proxy', proxy_port)]
                        for route, port in routes:
                            calls.clear()
                            paths.clear()
                            name = f'{mode}-{args.provider_route}-{route}'
                            output = result_dir/f'{name}.json'
                            env = dict(os.environ, OPENHANDS_SUPPRESS_BANNER='1', PYTHONPATH=os.pathsep.join(str(Path(checkout)/p) for p in ['openhands-sdk','openhands-tools','openhands-workspace','openhands-agent-server']))
                            command = [sys.executable, str(Path(__file__).resolve()),'client','--url',f'http://127.0.0.1:{port}/v1','--result',str(output)]
                            if asynchronous:
                                command.append('--asynchronous')
                            with (result_dir/f'{name}.log').open('w') as client_log:
                                completed = subprocess.run(command,env=env,cwd=tmp,stdout=client_log,stderr=subprocess.STDOUT,timeout=90)
                            row={'case':name,'exit':completed.returncode,'provider_calls':len(calls),'provider_paths':list(paths),'offsets':[round(t-calls[0],3) for t in calls]}
                            if completed.returncode == 0:
                                row.update(json.loads(output.read_text()))
                            results.append(row)
                            (result_dir/'results.json').write_text(json.dumps({'proxy_version':args.proxy_version,'provider_route':args.provider_route,'results':results},indent=2))
                            print(json.dumps(row),flush=True)
                            assert completed.returncode == 0, row
                            assert Path(row['retry_module']).is_relative_to(Path(checkout)), row
                            assert len(calls)==(3 if mode.startswith('baseline') else 2), row
                            if not mode.startswith('baseline'):
                                assert calls[1]-calls[0]>=12, row
                            if route == 'proxy':
                                assert row['events'][0]['hints'].get('llm_provider-retry-after') == '12', row
                finally:
                    os.killpg(proxy.pid,signal.SIGTERM)
                    try:
                        proxy.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(proxy.pid,signal.SIGKILL)
                        proxy.wait(timeout=5)
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
    print('PASS: all owned servers stopped.',flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    c=sub.add_parser('client');c.add_argument('--url');c.add_argument('--result');c.add_argument('--asynchronous',action='store_true')
    p=sub.add_parser('probe');p.add_argument('--baseline',required=True);p.add_argument('--candidate',required=True);p.add_argument('--out',required=True);p.add_argument('--proxy-version',default='1.103.6');p.add_argument('--candidate-only',action='store_true');p.add_argument('--provider-route',choices=['openai','azure'],default='openai')
    args=parser.parse_args()
    client(args) if args.action=='client' else probe(args)
