"""Authenticated AgentCore inference endpoint; source media/SQLite stay local.

Run from repo root: python -m integrations.aws.agentcore_app
The Dockerfile installs the same module as /app/agentcore_app.py.
"""
import copy
import json
import os
import tempfile
from pathlib import Path

from backend.aws_ai import AWSAI, converse_messages


def validate_request(payload):
    if not isinstance(payload, dict) or payload.get('protocol') != 1:
        raise ValueError('Expected Previously inference protocol 1')
    request = payload.get('request')
    if not isinstance(request, dict) or len(json.dumps(request).encode()) > 20 * 1024 * 1024:
        raise ValueError('Missing or oversized inference request')
    if set(request) - {'model', 'messages', 'tools', 'tool_choice', 'temperature', 'max_completion_tokens'}:
        raise ValueError('Unexpected inference options')
    allowed_models = {os.getenv('AWS_BEDROCK_MODEL_ID', 'amazon.nova-pro-v1:0'),
                      os.getenv('AWS_BEDROCK_REVIEW_MODEL_ID', '')}
    if request.get('model') not in allowed_models or not request.get('model'):
        raise ValueError('Model is not enabled on this endpoint')
    tokens = request.get('max_completion_tokens')
    if type(tokens) is not int or not 1 <= tokens <= 8192:
        raise ValueError('max_completion_tokens must be 1-8192')
    temperature = request.get('temperature', .1)
    if type(temperature) not in (int, float) or not 0 <= temperature <= 1:
        raise ValueError('Invalid temperature')
    if not isinstance(request.get('messages'), list) or not 1 <= len(request['messages']) <= 8:
        raise ValueError('Expected 1-8 evidence messages')
    tools = request.get('tools')
    if not isinstance(tools, list) or len(tools) != 1 or tools[0].get('type') != 'function':
        raise ValueError('Only the structured result function is accepted')
    function = tools[0].get('function', {})
    if function.get('name') != 'emit_result' or not isinstance(function.get('parameters'), dict):
        raise ValueError('Expected emit_result schema')
    if function['parameters'].get('type') != 'object':
        raise ValueError('Result must be an object')
    converse_messages(request)  # Reject remote image URLs, oversized images, invalid roles.
    return copy.deepcopy(request)


def invoke(payload, worker_factory=AWSAI):
    request = validate_request(payload)
    engine = os.getenv('AWS_AGENTCORE_ENGINE', 'strands')
    if engine not in ('bedrock', 'strands'):
        raise ValueError('AWS_AGENTCORE_ENGINE must be bedrock or strands; nested AgentCore is forbidden')
    # Runtime scratch is not a persistent catalog. Laptop caches the validated response.
    runtime = Path(tempfile.gettempdir()) / 'previously-agentcore'
    worker = worker_factory(runtime, model=request['model'], engine=engine)
    worker.prepare()
    status, text, _ = worker._send(request)
    if status != 200:
        raise RuntimeError('AWS inference failed')
    response = json.loads(text)
    # The laptop applies the original task-aware shape/evidence checks and bounded repairs.
    return {'protocol': 1, 'response': response}


def main():
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    # SDK responses also go to managed CloudWatch stdout; no credential objects.
    os.environ.setdefault('PREVIOUSLY_AUDIT_STDOUT', '1')
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def entrypoint(payload):
        return invoke(payload)

    app.run()


if __name__ == '__main__':
    main()
