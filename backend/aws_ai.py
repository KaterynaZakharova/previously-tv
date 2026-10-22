"""Optional AWS transport. Imported only in AWS mode; no cloud calls at import.

The Nova evidence/schema/cache/retry contract is shared across all transports.
AWS credentials are resolved by boto3, never copied into prompts or audit logs.
"""
import base64
import importlib.util
import io
import json
import os
import re
import urllib.error
import uuid
from typing import Literal

from .ai import Nova, AIUnavailable, AIResponseInvalid


def converse_messages(payload):
    """Convert labeled inline JPEG/PNG evidence to Bedrock's binary blocks."""
    messages, system = [], []
    image_count = 0
    for message in payload['messages']:
        role = message['role']
        if role == 'system':
            if not isinstance(message['content'], str):
                raise ValueError('System evidence must be text')
            system.append({'text': message['content']})
            continue
        if role not in ('user', 'assistant'):
            raise ValueError('Unsupported message role')
        content = message['content']
        if isinstance(content, str):
            content = [{'type': 'text', 'text': content}]
        blocks = []
        for block in content:
            if block['type'] == 'text':
                blocks.append({'text': block['text']})
            elif block['type'] == 'image_url':
                match = re.fullmatch(r'data:image/(jpeg|png|gif|webp);base64,(.+)', block['image_url']['url'], re.DOTALL)
                if not match:
                    raise ValueError('AWS evidence images must be inline base64 images; external URLs are refused')
                image = base64.b64decode(match[2], validate=True)
                if len(image) > 3_932_160:
                    raise ValueError('A frame exceeds the Bedrock image size limit')
                image_count += 1
                blocks.append({'image': {'format': match[1], 'source': {'bytes': image}}})
            else:
                raise ValueError('Unsupported evidence block')
        # Repair prompts can follow a user message; preserve them in one turn.
        if messages and messages[-1]['role'] == role:
            messages[-1]['content'].extend(blocks)
        else:
            messages.append({'role': role, 'content': blocks})
    if image_count > 20:
        raise ValueError('A Bedrock request supports at most 20 evidence images')
    return system, messages


def result_envelope(content, stop_reason='end_turn'):
    """Normalize wire format only. Shared schema/semantic checks remain mandatory."""
    calls = []
    texts = []
    for block in content:
        if 'toolUse' in block:
            tool = block['toolUse']
            calls.append({'type': 'function', 'function': {
                'name': tool['name'], 'arguments': json.dumps(tool['input'], ensure_ascii=False)}})
        if 'text' in block:
            texts.append(block['text'])
    finish = 'tool_calls' if calls else 'stop'
    if stop_reason not in ('tool_use', 'end_turn', 'stop_sequence'):
        finish = 'length' if stop_reason == 'max_tokens' else 'error'
    message = {'content': '\n'.join(texts)}
    if calls:
        message['tool_calls'] = calls
    return {'choices': [{'finish_reason': finish, 'message': message}]}


def schema_model(schema, name='RecapResult'):
    """Build a Strands/Pydantic result model from the app's small schema subset.

    Local Nova.validate_schema is still the final shape validator. Unknown schema
    features fail rather than silently weakening an output contract.
    """
    from pydantic import ConfigDict, Field, create_model
    from typing import List, Optional

    def annotation(node, path):
        allowed = {'type', 'properties', 'required', 'additionalProperties', 'items',
                   'enum', 'minItems', 'maxItems', 'description'}
        if set(node) - allowed:
            raise ValueError('Unsupported structured-output schema at ' + path)
        if node.get('enum'):
            return Literal[tuple(node['enum'])]
        kind = node.get('type')
        if kind == 'object':
            fields = {}
            for key, child in node.get('properties', {}).items():
                field_type = annotation(child, path + '_' + key)
                if key in node.get('required', []):
                    fields[key] = (field_type, ...)
                else:
                    fields[key] = (Optional[field_type], None)
            return create_model(path, __config__=ConfigDict(extra='forbid', strict=True), **fields)
        if kind == 'array':
            from typing import Annotated
            return Annotated[List[annotation(node['items'], path + '_item')],
                             Field(min_length=node.get('minItems'), max_length=node.get('maxItems'))]
        types = {'string': str, 'integer': int, 'number': float, 'boolean': bool, 'null': type(None)}
        if kind not in types:
            raise ValueError('Unsupported schema type at ' + path)
        return types[kind]

    return annotation(schema, name)


class AWSAI(Nova):
    """AWS mode: Bedrock Converse, Strands over Bedrock, or AgentCore Runtime."""
    provider = 'aws'
    label = 'Amazon Bedrock · Nova'
    key_env = 'AWS_PROFILE / AWS credentials'
    supports_vision = True

    def __init__(self, runtime, model=None, engine=None, client=None, session=None):
        selected = model or os.getenv('AWS_BEDROCK_MODEL_ID', 'amazon.nova-pro-v1:0')
        super().__init__(runtime, key='', model=selected)
        self.engine = engine or os.getenv('AWS_AI_ENGINE', 'strands').strip().lower()
        if self.engine not in ('bedrock', 'strands', 'agentcore'):
            raise AIUnavailable('AWS_AI_ENGINE must be bedrock, strands or agentcore')
        self.region = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1'
        self.runtime_arn = os.getenv('AWS_AGENTCORE_RUNTIME_ARN', '')
        self.qualifier = os.getenv('AWS_AGENTCORE_QUALIFIER', 'DEFAULT')
        self._session = session
        self._client = client
        self.endpoint = ('agentcore://' + self.runtime_arn if self.engine == 'agentcore'
                         else 'bedrock://' + self.region + '/' + selected)
        self.label = {'bedrock': 'Amazon Bedrock · Nova', 'strands': 'Strands · Amazon Bedrock · Nova',
                      'agentcore': 'AgentCore · Amazon Bedrock · Nova'}[self.engine]

    @property
    def configured(self):
        sdk = self._client is not None or importlib.util.find_spec('boto3') is not None
        strands = self.engine != 'strands' or importlib.util.find_spec('strands') is not None
        return bool(sdk and strands and self.model and (self.engine != 'agentcore' or self.runtime_arn))

    def configuration_error(self):
        if importlib.util.find_spec('boto3') is None or (self.engine == 'strands' and importlib.util.find_spec('strands') is None):
            return 'AI unavailable — install optional AWS packages: python -m pip install -r requirements-aws.txt'
        if self.engine == 'agentcore' and not self.runtime_arn:
            return 'AI unavailable — set AWS_AGENTCORE_RUNTIME_ARN or select AWS_AI_ENGINE=bedrock/strands'
        return 'AI unavailable — configure AWS credentials and an accessible Nova vision model in AWS_BEDROCK_MODEL_ID'

    def cache_namespace(self):
        return '|'.join((self.provider, self.engine, self.region, self.runtime_arn, self.qualifier))

    def reviewer(self):
        # AgentCore has a server-bound model; configure review model there too.
        model = os.getenv('AWS_BEDROCK_REVIEW_MODEL_ID') or self.model
        return AWSAI(self.runtime, model=model, engine=self.engine, client=self._client, session=self._session)

    def prepare(self):
        if self._client is not None:
            return
        if self.engine == 'agentcore' and not self.runtime_arn:
            raise AIUnavailable('Set AWS_AGENTCORE_RUNTIME_ARN or choose AWS_AI_ENGINE=bedrock/strands')
        try:
            import boto3
            from botocore.config import Config
            if self.engine == 'strands' and importlib.util.find_spec('strands') is None:
                raise ImportError('strands')
            if self._session is None:
                self._session = boto3.Session(region_name=self.region)
            if self._session.get_credentials() is None:
                raise AIUnavailable('AWS credentials unavailable. Configure your AWS profile and sign in again.')
            service = 'bedrock-agentcore' if self.engine == 'agentcore' else 'bedrock-runtime'
            self._client = self._session.client(service, region_name=self.region, config=Config(
                connect_timeout=15, read_timeout=180, retries={'total_max_attempts': 1}))
        except ImportError:
            raise AIUnavailable('Install AWS dependencies: python -m pip install -r requirements-aws.txt') from None
        except AIUnavailable:
            raise
        except Exception as error:
            self.log({'task': 'aws_credentials', 'status': 'failed', 'error_type': type(error).__name__})
            raise AIUnavailable('AWS credentials/profile unavailable. Check AWS_PROFILE and your AWS login.') from None

    def list_models(self):
        # Deliberately no ListFoundationModels permission/model auto-selection.
        return list(dict.fromkeys([self.model, os.getenv('AWS_BEDROCK_REVIEW_MODEL_ID') or self.model]))

    def _send(self, payload):
        self.prepare()
        try:
            if self.engine == 'agentcore':
                return self._agentcore(payload)
            if self.engine == 'strands':
                return self._strands(payload)
            return self._bedrock(payload)
        except (AIUnavailable, AIResponseInvalid, urllib.error.HTTPError):
            raise
        except Exception as error:
            # Map AWS errors into the existing bounded HTTP retry policy.
            source_error = error
            for _ in range(4):
                if isinstance(getattr(source_error, 'response', None), dict) or source_error.__cause__ is None:
                    break
                source_error = source_error.__cause__
            response = getattr(source_error, 'response', None)
            if isinstance(response, dict) and 'Error' in response:
                native = response['Error']
                code = native.get('Code', 'AWSFailure')
                status = response.get('ResponseMetadata', {}).get('HTTPStatusCode', 500)
                if code in ('ThrottlingException', 'TooManyRequestsException', 'ServiceQuotaExceededException'):
                    status = 429
                headers = response.get('ResponseMetadata', {}).get('HTTPHeaders', {})
                # Log service error content; never serialize an SDK/session/credential object.
                self.log({'task': getattr(self.local, 'task', 'aws'), 'status': 'aws_error', 'error': native})
                body = json.dumps({'error': {'code': code, 'message': native.get('Message', code)}}).encode()
                raise urllib.error.HTTPError(self.endpoint, status, code, headers, io.BytesIO(body)) from None
            if isinstance(error, (ValueError, KeyError, TypeError)):
                raise AIResponseInvalid('AWS request/output contract failed: ' + str(error)[:250]) from None
            self.log({'task': getattr(self.local, 'task', 'aws'), 'status': 'failed', 'error_type': type(error).__name__})
            raise AIUnavailable('AWS inference unavailable (' + type(error).__name__ + '). Check credentials, region and model access.') from None

    def _bedrock(self, payload):
        system, messages = converse_messages(payload)
        function = payload['tools'][0]['function']
        response = self._client.converse(
            modelId=self.model, system=system, messages=messages,
            inferenceConfig={'temperature': payload.get('temperature', .1), 'maxTokens': payload['max_completion_tokens']},
            toolConfig={'tools': [{'toolSpec': {'name': 'emit_result',
                         'description': function['description'], 'inputSchema': {'json': function['parameters']}}}],
                        'toolChoice': {'tool': {'name': 'emit_result'}}})
        self.log({'task': getattr(self.local, 'task', 'aws'), 'status': 'aws_response', 'response': response})
        normalized = result_envelope(response['output']['message']['content'], response.get('stopReason', ''))
        headers = response.get('ResponseMetadata', {}).get('HTTPHeaders', {})
        return 200, json.dumps(normalized, ensure_ascii=False), headers

    def _strands(self, payload):
        try:
            from strands import Agent
            from strands.models import BedrockModel
        except ImportError:
            raise AIUnavailable('Install Strands: python -m pip install -r requirements-aws.txt') from None
        system, messages = converse_messages(payload)
        output_model = schema_model(payload['tools'][0]['function']['parameters'])
        from botocore.config import Config
        model = BedrockModel(model_id=self.model, boto_session=self._session,
                             boto_client_config=Config(connect_timeout=15, read_timeout=180, retries={'total_max_attempts': 1}),
                             streaming=False, max_tokens=payload['max_completion_tokens'], temperature=payload.get('temperature', .1))
        # One fresh agent per evidence task: no cross-viewer conversation memory.
        agent = Agent(model=model, callback_handler=None, retry_strategy=None, system_prompt='\n'.join(b['text'] for b in system))
        result = None
        try:
            result = agent(messages, structured_output_model=output_model, limits={'turns': 4})
        finally:
            self.log({'task': getattr(self.local, 'task', 'aws'), 'status': 'strands_response',
                      'messages': json.loads(json.dumps(agent.messages, default=lambda v: '<binary frame>')),
                      'stop_reason': getattr(result, 'stop_reason', 'failed')})
        if result.structured_output is None:
            raise AIResponseInvalid('Strands returned no structured result; try later.')
        arguments = result.structured_output.model_dump(mode='json', exclude_unset=True)
        normalized = result_envelope([{'toolUse': {'name': 'emit_result', 'input': arguments}}], 'tool_use')
        return 200, json.dumps(normalized, ensure_ascii=False), {}

    def _agentcore(self, payload):
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn, qualifier=self.qualifier,
            runtimeSessionId=str(uuid.uuid4()), contentType='application/json', accept='application/json',
            payload=json.dumps({'protocol': 1, 'request': payload}, ensure_ascii=False).encode())
        stream = response['response']
        try:
            body = stream.read()
        finally:
            stream.close()
        status = response.get('statusCode', 200)
        if status != 200:
            raise urllib.error.HTTPError(self.endpoint, status, 'AgentCore inference failed', {}, io.BytesIO(body))
        result = json.loads(body)
        if result.get('protocol') != 1 or not isinstance(result.get('response'), dict):
            raise AIResponseInvalid('Unexpected AgentCore response contract')
        self.log({'task': getattr(self.local, 'task', 'aws'), 'status': 'agentcore_response', 'response': result})
        return 200, json.dumps(result['response'], ensure_ascii=False), {}
