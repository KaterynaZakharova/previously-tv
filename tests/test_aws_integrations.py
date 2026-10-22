"""Transport/SDK contracts with deterministic service doubles"""

import argparse
import base64
import builtins
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend.ai import (
    AIResponseInvalid,
    AIUnavailable,
    Nova,
    make_ai,
    is_configured,
    supports_vision,
)
from backend.aws_ai import AWSAI, converse_messages, schema_model
from backend import schemas
from integrations.aws.agentcore_app import invoke, validate_request
from tools.aws_assets import upload, download, asset_path
from tools.submit_aws_processing import request_for

SCHEMA = schemas.obj(ok={'type': 'boolean'}, message={'type': 'string'})
EXPECTED = {'ok': True, 'message': 'Verified evidence'}


def native_result(value, finish='tool_use', name='emit_result'):
    return {
        'output': {
            'message': {
                'role': 'assistant',
                'content': [
                    {
                        'toolUse': {
                            'toolUseId': 'test-result',
                            'name': name,
                            'input': value,
                        }
                    }
                ],
            }
        },
        'stopReason': finish,
        'usage': {'inputTokens': 80, 'outputTokens': 20, 'totalTokens': 100},
        'metrics': {'latencyMs': 1},
        'ResponseMetadata': {'HTTPStatusCode': 200, 'HTTPHeaders': {}},
    }


class BedrockDouble:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [native_result(EXPECTED)])

    def converse(self, **request):
        self.calls.append(copy.deepcopy(request))
        response = self.results.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AWSModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_local_mode_does_not_import_aws_sdks(self):
        real = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in (
                'boto3',
                'botocore',
                'strands',
                'bedrock_agentcore',
            ):
                raise AssertionError('Local mode imported ' + name)
            return real(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=guarded):
            ai = make_ai(self.root)
            self.assertIsInstance(ai, Nova)
            self.assertFalse(is_configured(ai))

    def test_factory_is_explicit_and_never_falls_back(self):
        os.environ['RUN_MODE'] = 'aws'
        os.environ['AWS_AI_ENGINE'] = 'bedrock'
        self.assertIsInstance(make_ai(self.root), AWSAI)
        os.environ['RUN_MODE'] = 'awss'
        with self.assertRaises(AIUnavailable):
            make_ai(self.root)

    def test_bedrock_preserves_frames_labels_schema_and_cache(self):
        client = BedrockDouble()
        ai = AWSAI(self.root, engine='bedrock', client=client)
        data = {
            'transcript': [
                {'id': 4, 'start': 12.1, 'end': 15.3, 'text': 'Recall this'}
            ],
            '_images': [
                {
                    'label': 'SOURCE FRAME 13.5s',
                    'url': 'data:image/jpeg;base64,'
                    + base64.b64encode(b'frame-bytes').decode(),
                }
            ],
        }
        self.assertTrue(supports_vision(ai))
        for _ in range(2):
            self.assertEqual(
                ai.json('test', 'Assess supplied evidence', data, SCHEMA), EXPECTED
            )
        self.assertEqual(len(client.calls), 1)
        request = client.calls[0]
        self.assertEqual(
            request['toolConfig']['toolChoice'], {'tool': {'name': 'emit_result'}}
        )
        self.assertEqual(
            request['toolConfig']['tools'][0]['toolSpec']['inputSchema']['json'], SCHEMA
        )
        content = request['messages'][0]['content']
        self.assertEqual(content[-1]['image']['source']['bytes'], b'frame-bytes')
        self.assertIn('13.5s', content[-2]['text'])
        self.assertIn('12.1', content[0]['text'])
        self.assertNotIn('Authorization', json.dumps(client.calls, default=str))
        log = (self.root / 'logs/ai-responses.jsonl').read_text()
        self.assertIn('aws_response', log)
        self.assertIn('cache_hit', log)

    def test_invalid_result_repaired_then_validated_not_silently_filled(self):
        client = BedrockDouble(
            [native_result({'ok': 'invalid'}), native_result(EXPECTED)]
        )
        ai = AWSAI(self.root, engine='bedrock', client=client)
        self.assertEqual(ai.json('test', 'Assess', {}, SCHEMA), EXPECTED)
        self.assertEqual(len(client.calls), 2)
        repaired = client.calls[1]['messages']
        self.assertEqual(
            len(repaired), 1
        )  # consecutive user messages merged for Converse
        self.assertIn('missing required fields', repaired[0]['content'][-1]['text'])

    def test_truncated_output_is_never_cached(self):
        client = BedrockDouble([native_result(EXPECTED, 'max_tokens')] * 3)
        ai = AWSAI(self.root, engine='bedrock', client=client)
        with self.assertRaises(AIResponseInvalid):
            ai.json('test', 'Assess', {}, SCHEMA)
        self.assertFalse(list((self.root / 'cache').glob('*.json')))

    def test_cache_isolates_region_engine_and_endpoint(self):
        first = AWSAI(self.root, engine='bedrock', client=BedrockDouble())
        os.environ['AWS_REGION'] = 'us-west-2'
        other = AWSAI(self.root, engine='bedrock', client=BedrockDouble())
        self.assertNotEqual(first.cache_namespace(), other.cache_namespace())
        other.engine = 'agentcore'
        self.assertNotEqual(first.cache_namespace(), other.cache_namespace())

    def test_aws_reviewer_keeps_transport_not_direct_nova_key(self):
        ai = AWSAI(self.root, engine='bedrock', client=BedrockDouble())
        os.environ['NOVA_API_KEY'] = 'must-not-be-used'
        os.environ['AWS_BEDROCK_REVIEW_MODEL_ID'] = 'amazon.nova-lite-v1:0'
        reviewer = ai.reviewer()
        self.assertIsInstance(reviewer, AWSAI)
        self.assertEqual(reviewer.model, 'amazon.nova-lite-v1:0')
        self.assertEqual(reviewer.engine, 'bedrock')
        self.assertEqual(reviewer.key, '')

    def test_evidence_rejects_external_images_and_more_than_20_frames(self):
        ai = Nova(self.root, key='test')
        payload = ai.build_payload(
            'test',
            'Assess',
            {'_images': [{'label': 'x', 'url': 'https://example.com/x.jpg'}]},
            SCHEMA,
            50,
        )
        with self.assertRaisesRegex(ValueError, 'inline'):
            converse_messages(payload)
        image = {'label': 'x', 'url': 'data:image/jpeg;base64,eA=='}
        payload = ai.build_payload(
            'test', 'Assess', {'_images': [image] * 21}, SCHEMA, 50
        )
        with self.assertRaisesRegex(ValueError, '20'):
            converse_messages(payload)

    def test_agentcore_client_server_roundtrip_without_cloud(self):
        outer = self
        os.environ['AWS_AGENTCORE_RUNTIME_ARN'] = (
            'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test'
        )

        class RuntimeDouble:
            def invoke_agent_runtime(self, **kwargs):
                outer.assertEqual(len(kwargs['runtimeSessionId']), 36)
                outer.assertEqual(kwargs['contentType'], 'application/json')

                def factory(runtime, **options):
                    return AWSAI(
                        outer.root / 'remote',
                        model=options['model'],
                        engine='bedrock',
                        client=BedrockDouble(),
                    )

                result = invoke(json.loads(kwargs['payload']), worker_factory=factory)
                return {
                    'response': io.BytesIO(json.dumps(result).encode()),
                    'statusCode': 200,
                }

        ai = AWSAI(self.root, engine='agentcore', client=RuntimeDouble())
        self.assertEqual(ai.json('test', 'Assess', {}, SCHEMA), EXPECTED)

    def test_endpoint_rejects_unapproved_model_nested_runtime_and_arbitrary_tools(self):
        request = Nova(
            self.root, key='test', model='amazon.nova-pro-v1:0'
        ).build_payload('test', 'Assess', {}, SCHEMA, 50)
        request['model'] = 'unapproved'
        with self.assertRaisesRegex(ValueError, 'Model'):
            validate_request({'protocol': 1, 'request': request})
        request['model'] = 'amazon.nova-pro-v1:0'
        request['tools'][0]['function']['name'] = 'run_shell'
        with self.assertRaisesRegex(ValueError, 'emit_result'):
            validate_request({'protocol': 1, 'request': request})
        request['tools'][0]['function']['name'] = 'emit_result'
        os.environ['AWS_AGENTCORE_ENGINE'] = 'agentcore'
        with self.assertRaisesRegex(ValueError, 'nested'):
            invoke({'protocol': 1, 'request': request})


@unittest.skipUnless(
    importlib.util.find_spec('strands'), 'Optional AWS SDK not installed'
)
class RealSDKTests(unittest.TestCase):
    def test_pydantic_schema_covers_empty_cast_and_candidate_ids(self):
        from pydantic import ValidationError

        model = schema_model(schemas.scenes_for(set(), [0], []))
        row = dict(
            index=0,
            category='secondary',
            title='Actual frame',
            summary='Visible action',
            why='Context',
            characters=[],
            visual_evidence='Frame at 1.2s',
            evidence_cue_ids=[],
        )
        self.assertEqual(
            model.model_validate({'scenes': [row]}).model_dump()['scenes'][0][
                'characters'
            ],
            [],
        )
        with self.assertRaises(ValidationError):
            model.model_validate({'scenes': [dict(row, characters=['invented'])]})
        queue = schema_model(schemas.queue_for_candidates(['e001_00']))
        with self.assertRaises(ValidationError):
            queue.model_validate(
                {
                    'explanation': 'why',
                    'items': [{'event_id': 'e00_00', 'reason': 'x', 'ranges': []}],
                }
            )

    def test_real_boto3_service_shapes_accept_native_converse_request(self):
        import boto3
        from botocore.stub import Stubber, ANY

        with tempfile.TemporaryDirectory() as root:
            client = boto3.client(
                'bedrock-runtime',
                region_name='us-east-1',
                aws_access_key_id='test',
                aws_secret_access_key='test',
            )
            with Stubber(client) as stub:
                stub.add_response(
                    'converse',
                    native_result(EXPECTED),
                    {
                        'modelId': 'amazon.nova-pro-v1:0',
                        'messages': ANY,
                        'system': ANY,
                        'inferenceConfig': ANY,
                        'toolConfig': ANY,
                    },
                )
                ai = AWSAI(root, engine='bedrock', client=client)
                self.assertEqual(ai.json('test', 'Assess', {}, SCHEMA), EXPECTED)
                stub.assert_no_pending_responses()

    def test_real_strands_loop_with_fake_bedrock_transport(self):
        # Run the actual Agent, BedrockModel, structured-output tool and Pydantic.
        # Only the network-facing boto3 client is replaced.
        class StrandsClient:
            def __init__(self):
                self.calls = []
                self.meta = SimpleNamespace(region_name='us-east-1')

            def converse(self, **request):
                self.calls.append(request)
                specs = request.get('toolConfig', {}).get('tools', [])
                name = next(s['toolSpec']['name'] for s in specs if 'toolSpec' in s)
                return native_result(EXPECTED, name=name)

        class Session:
            region_name = 'us-east-1'

            def __init__(self):
                self.client_double = StrandsClient()

            def client(self, *args, **kwargs):
                return self.client_double

        with tempfile.TemporaryDirectory() as root:
            session = Session()
            ai = AWSAI(
                root, engine='strands', session=session, client=session.client_double
            )
            self.assertEqual(ai.json('test', 'Assess', {}, SCHEMA), EXPECTED)
            self.assertTrue(session.client_double.calls)
            self.assertIn(
                'strands_response', (Path(root) / 'logs/ai-responses.jsonl').read_text()
            )

    def test_runtime_deployment_and_processing_match_installed_service_shapes(self):
        import boto3
        from botocore.validate import validate_parameters

        session = boto3.Session(
            aws_access_key_id='test',
            aws_secret_access_key='test',
            region_name='us-east-1',
        )
        root = Path(__file__).resolve().parents[1]
        request = json.loads(
            (root / 'integrations/aws/runtime.example.json')
            .read_text()
            .replace('ACCOUNT_ID', '123456789012')
        )
        client = session.client('bedrock-agentcore-control')
        validate_parameters(
            request,
            client.meta.service_model.operation_model('CreateAgentRuntime').input_shape,
        )
        args = argparse.Namespace(
            name='previously-test',
            output='s3://bucket/results',
            prefix='shared',
            role='arn:aws:iam::123456789012:role/PreviouslyProcessingRole',
            image='123456789012.dkr.ecr.us-east-1.amazonaws.com/previously:latest',
            instance_type='ml.m5.xlarge',
            engine='strands',
            region='us-east-1',
            model='amazon.nova-pro-v1:0',
            bucket='bucket',
            movie=None,
            review_clips=False,
        )
        client = session.client('sagemaker')
        validate_parameters(
            request_for(args),
            client.meta.service_model.operation_model(
                'CreateProcessingJob'
            ).input_shape,
        )

    def test_real_agentcore_sdk_http_surface(self):
        from bedrock_agentcore.runtime import BedrockAgentCoreApp
        from starlette.testclient import TestClient

        with tempfile.TemporaryDirectory() as root:
            app = BedrockAgentCoreApp()

            def factory(runtime, **options):
                return AWSAI(
                    root,
                    model=options['model'],
                    engine='bedrock',
                    client=BedrockDouble(),
                )

            @app.entrypoint
            def handle(payload):
                return invoke(payload, worker_factory=factory)

            request = Nova(
                root, key='test', model='amazon.nova-pro-v1:0'
            ).build_payload('test', 'Assess', {}, SCHEMA, 50)
            with TestClient(app) as client:
                self.assertEqual(client.get('/ping').status_code, 200)
                response = client.post(
                    '/invocations', json={'protocol': 1, 'request': request}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Nova.parse(response.json()['response']), EXPECTED)

    def test_real_throttling_uses_shared_retry_policy(self):
        from botocore.exceptions import ClientError

        error = ClientError(
            {
                'Error': {'Code': 'ThrottlingException', 'Message': 'Busy'},
                'ResponseMetadata': {'HTTPStatusCode': 429},
            },
            'Converse',
        )
        with tempfile.TemporaryDirectory() as root:
            client = BedrockDouble([error, native_result(EXPECTED)])
            ai = AWSAI(root, engine='bedrock', client=client)
            with patch.object(ai, 'wait_until'):
                self.assertEqual(ai.json('test', 'Assess', {}, SCHEMA), EXPECTED)
            self.assertEqual(len(client.calls), 2)


class S3Double:
    def __init__(self):
        self.objects = {}

    def upload_file(self, source, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = Path(source).read_bytes()

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.objects[(Bucket, Key)])}

    def download_file(self, bucket, key, target):
        Path(target).write_bytes(self.objects[(bucket, key)])


class AssetAndBatchTests(unittest.TestCase):
    def test_s3_roundtrip_preserves_bytes_and_excludes_private_state(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root, target = Path(a), Path(b)
            (root / 'assets/cartoon/demo').mkdir(parents=True)
            video = root / 'assets/cartoon/demo/movie.mp4'
            video.write_bytes(b'original-exact-video-bytes')
            with sqlite3.connect(root / 'assets/db.sqlite') as db:
                db.execute('create table catalog(id)')
            (root / 'assets/analysis').mkdir()
            (root / 'assets/analysis/ai-responses.jsonl').write_text('private-response')
            (root / 'runtime').mkdir()
            (root / 'runtime/viewers.sqlite3').write_text('private-viewer')
            client = S3Double()
            self.assertEqual(upload(root, client, 'bucket', 'shared')['files'], 2)
            self.assertEqual(
                download(target, client, 'bucket', 'shared')['downloaded'], 2
            )
            self.assertEqual(
                (target / video.relative_to(root)).read_bytes(), video.read_bytes()
            )
            self.assertFalse((target / 'runtime').exists())
            self.assertEqual(
                download(target, client, 'bucket', 'shared')['unchanged'], 2
            )

    def test_s3_rejects_lfs_pointer_and_path_escape(self):
        with tempfile.TemporaryDirectory() as a:
            root = Path(a)
            (root / 'assets').mkdir()
            (root / 'assets/movie.mp4').write_text(
                'version https://git-lfs.github.com/spec/v1\noid sha256:bad\nsize 100000\n'
            )
            with self.assertRaisesRegex(ValueError, 'Git LFS'):
                upload(root, S3Double(), 'bucket', 'shared')
            for path in ('runtime/viewers.sqlite3', 'assets/../../.env', 'assets/C:/x'):
                with self.assertRaises(ValueError):
                    asset_path(root, path)

    def test_s3_checks_all_hashes_before_replacing_any_file(self):
        with tempfile.TemporaryDirectory() as a:
            root = Path(a)
            (root / 'assets').mkdir()
            first = root / 'assets/one.txt'
            first.write_text('old')
            client = S3Double()
            digest = hashlib.sha256(b'new').hexdigest()
            client.objects[('b', 'p/manifest.json')] = json.dumps(
                {
                    'version': 1,
                    'files': [
                        {'path': 'assets/one.txt', 'sha256': digest, 'size': 3},
                        {'path': 'assets/two.txt', 'sha256': '0' * 64, 'size': 4},
                    ],
                }
            ).encode()
            client.objects[('b', 'p/objects/' + digest)] = b'new'
            client.objects[('b', 'p/objects/' + '0' * 64)] = b'bad!'
            with self.assertRaises(ValueError):
                download(root, client, 'b', 'p', replace=True)
            self.assertEqual(first.read_text(), 'old')

    def test_processing_request_limits_runtime_and_exports_only_derived_output(self):
        args = argparse.Namespace(
            name='previously-test',
            output='s3://b/results',
            prefix='shared',
            role='role',
            image='image',
            instance_type='ml.m5.xlarge',
            engine='strands',
            region='us-east-1',
            model='amazon.nova-pro-v1:0',
            bucket='b',
            movie=['spring'],
            review_clips=False,
        )
        request = request_for(args)
        self.assertEqual(request['StoppingCondition']['MaxRuntimeInSeconds'], 3600)
        self.assertEqual(
            json.loads(request['Environment']['PREVIOUSLY_UNITS']), ['spring']
        )
        self.assertNotIn('NOVA_API_KEY', request['Environment'])


if __name__ == '__main__':
    unittest.main()
