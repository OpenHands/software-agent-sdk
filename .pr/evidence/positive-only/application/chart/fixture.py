"""Controlled billing backend for two real LiteLLM proxies; no paid inference."""
import json
import os
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

CASES = {c['id']: c for c in json.loads(Path(__file__).with_name('cases.json').read_text())}
LEDGER = []
LOCK = Lock()


def case_from_messages(messages, default=None):
    for message in messages:
        content = message.get('content') or ''
        if isinstance(content, list):
            content = ' '.join(c.get('text', '') for c in content if isinstance(c, dict))
        if not isinstance(content, str):
            continue
        match = re.search(r'OHE3895_CASE:([a-z_]+)', content)
        if match:
            if match[1] not in CASES:
                raise ValueError('Unknown billing case')
            return CASES[match[1]]
    if default is not None:
        return CASES[default]
    raise ValueError('Missing synthetic case marker')


def schema_value(schema, definitions=None, field=None):
    """Minimal valid synthetic values for the requested finish JSON schema."""
    definitions = definitions or schema.get('$defs', {})
    if '$ref' in schema:
        return schema_value(definitions[schema['$ref'].split('/')[-1]], definitions, field)
    if 'const' in schema:
        return schema['const']
    if schema.get('enum'):
        return next((v for v in ['completed', 'success', 'done', 'ok'] if v in schema['enum']), schema['enum'][0])
    if 'anyOf' in schema:
        return schema_value(next(s for s in schema['anyOf'] if s.get('type') != 'null'), definitions, field)
    kind = schema.get('type')
    if kind == 'object' or 'properties' in schema:
        return {name: schema_value(schema.get('properties', {}).get(name, {}), definitions, name) for name in schema.get('required', [])}
    if kind == 'array':
        return []
    if kind == 'boolean':
        return True
    if kind in ['number', 'integer']:
        return schema.get('minimum', 0)
    if kind == 'null':
        return None
    return 'completed' if field == 'status' else 'Synthetic billing task completed.'


def make_response(body):
    model = body.get('model', '').removeprefix('openai/')
    default_case = 'router_aggregate_usage' if model == 'cost-auto' else 'fixed_usage_only'
    case = case_from_messages(body.get('messages', []), default=default_case)
    serialized = json.dumps(body.get('messages', []))
    marker = re.search(r'OHE3895_RUN:([a-zA-Z0-9_-]{1,128})', serialized)
    run_id = marker[1] if marker else 'unscoped-validation'
    if model != case['model']:
        raise ValueError('Case/model mismatch')
    call_id = 'fixture-' + uuid4().hex
    stages = []
    if Decimal(case['selection_cost']):
        stages.append({'stage': 'selection', 'cost_usd': case['selection_cost']})
    stages.append({'stage': 'generation', 'cost_usd': case['generation_cost']})
    record = {
        'id': call_id, 'case': case['id'], 'model': model, 'run_id': run_id,
        'stages': stages,
        'ledger_total_usd': str(sum((Decimal(x['cost_usd']) for x in stages), Decimal(0))),
        'emitted_usage_cost': case['usage_cost'], 'emitted_header_cost': case['header_cost'],
        'stream': bool(body.get('stream')),
        'has_tools': bool(body.get('tools')),
        'tool_names': [t.get('function', {}).get('name') for t in body.get('tools', [])],
        'scope': 'Synthetic selection/generation charges; no live inference',
    }
    with LOCK:
        LEDGER.append(record)
    usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110, 'cost': float(case['usage_cost'])}
    base = {'id': call_id, 'model': model, 'created': 1}
    if body.get('tools'):
        finish = next((t.get('function', {}) for t in body['tools'] if t.get('function', {}).get('name') == 'finish'), {})
        arguments = schema_value(finish.get('parameters', {'type': 'object', 'required': ['message'], 'properties': {'message': {'type': 'string'}}}))
        message = {
            'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'finish-' + call_id, 'type': 'function',
                            'function': {'name': 'finish', 'arguments': json.dumps(arguments)}}],
        }
        reason = 'tool_calls'
    else:
        message = {'role': 'assistant', 'content': 'Synthetic billing task completed.'}
        reason = 'stop'
    if body.get('stream'):
        delta = dict(message)
        if 'tool_calls' in delta:
            delta['tool_calls'] = [dict(delta['tool_calls'][0], index=0)]
        chunks = [
            dict(base, object='chat.completion.chunk', choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]),
            dict(base, object='chat.completion.chunk', choices=[{'index': 0, 'delta': {}, 'finish_reason': reason}]),
            dict(base, object='chat.completion.chunk', choices=[], usage=usage),
        ]
        payload = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        content_type = 'text/event-stream'
    else:
        payload = json.dumps(dict(base, object='chat.completion', choices=[{'index': 0, 'message': message, 'finish_reason': reason}], usage=usage))
        content_type = 'application/json'
    return record, content_type, payload.encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/health':
            self.reply(200, {'status': 'ok'})
        elif self.path == '/ledger':
            with LOCK:
                records = list(LEDGER)
            self.reply(200, records)
        else:
            self.reply(404, {'error': 'unknown fixture route'})

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.reply(404, {'error': 'unknown fixture route'})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            record, content_type, payload = make_response(body)
        except (ValueError, KeyError, TypeError):
            self.reply(400, {'error': 'invalid synthetic billing request'})
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('x-ohe3895-fixture-id', record['id'])
        if record['emitted_header_cost'] is not None:
            self.send_header('x-litellm-response-cost', record['emitted_header_cost'])
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()
        print(json.dumps(record), flush=True)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
