"""Replay the user's provider schema rejection; all successful outputs are test fixtures."""
import copy, io, json, tempfile, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
from backend.ai import AIResponseInvalid, AIUnavailable, Nova
from backend import schemas

FAILURE=json.loads((Path(__file__).parent/'fixtures/schema_failure.json').read_text())
FAILED=json.loads(FAILURE['error']['failed_generation'])
def corrected():
    # Fictional successful provider fixture ONLY. Production never fills these fields.
    answer=copy.deepcopy(FAILED)
    for i,event in enumerate(answer['events']):
        event.update(title='Fixture event '+str(i),why='Test evidence only.',threads=['fixture_thread'])
    return answer
class Response(io.BytesIO):
    status=200
    def __init__(self,value,headers=None,finish='stop'):
        raw={'choices':[{'finish_reason':finish,'message':{'content':json.dumps(value)}}]}
        super().__init__(json.dumps(raw).encode());self.headers=headers or {}
def rejected(code=400,error=None,headers=None):
    return urllib.error.HTTPError('https://api.nova.amazon.com/',code,'test',headers or {},io.BytesIO(json.dumps(error or FAILURE).encode()))

class JSONRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ai=Nova(self.tmp.name,key='private-test-key',interval=0,model='nova-2-lite-v1')
        self.progress=[];self.now=1000.;self.sleeps=[]
        self.clock=patch('backend.ai.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        def advance(seconds):self.sleeps.append(seconds);self.now+=seconds
        self.sleep=patch('backend.ai.time.sleep',side_effect=advance);self.sleep.start()
    def tearDown(self):self.sleep.stop();self.clock.stop();self.tmp.cleanup()
    def call(self):
        return self.ai.json('extract','Extract from supplied cues only.',{'cues':[{'id':34,'text':'Memory overwrite in progress!'}]},schemas.ANALYSIS,lambda *x:self.progress.append(x),max_tokens=2900)
    def test_exact_reported_failed_generation_is_rejected_locally(self):
        with self.assertRaisesRegex(ValueError,r'\$\.events\[0\]: missing required fields title, why, threads'):
            Nova.validate_schema(FAILED,schemas.ANALYSIS)
    def test_provider_400_gets_ai_repair_not_local_defaults(self):
        expected=corrected();expected['events'][0]['title']='AI fixture chose this title'
        with patch('urllib.request.urlopen',side_effect=[rejected(),Response(expected)]) as request:
            actual=self.call()
        self.assertEqual(actual,expected);self.assertEqual(request.call_count,2)
        first,second=[json.loads(c.args[0].data) for c in request.call_args_list]
        self.assertEqual(first['tool_choice'],'required')
        self.assertEqual(first['tools'][0]['function']['parameters'],schemas.ANALYSIS)
        self.assertEqual(second['tools'],first['tools'])
        self.assertNotIn('response_format',second)
        self.assertIn('$.events[]: required keys title, summary, category, why',first['messages'][1]['content'])
        self.assertIn('emit_result',second['messages'][-1]['content'])
        self.assertIn('missing properties',second['messages'][-1]['content'])
        self.assertIn('Extract from supplied cues only.',second['messages'][1]['content'])
        self.assertTrue(any(stage=='repair' for stage,_ in self.progress))
        files=list((self.ai.runtime/'cache').glob('*.json'));self.assertEqual(len(files),1)
        self.assertEqual(Nova.checked_parse(json.loads(files[0].read_text()),schemas.ANALYSIS),expected)
        log=(self.ai.runtime/'logs/ai-responses.jsonl').read_text()
        self.assertIn('failed_generation',log);self.assertIn('schema_retry',log);self.assertNotIn('private-test-key',log)
    def test_repeated_generation_failure_is_bounded_and_never_cached(self):
        with patch('urllib.request.urlopen',side_effect=lambda *args,**kwargs: (_ for _ in ()).throw(rejected())) as request:
            with self.assertRaisesRegex(AIResponseInvalid,'two repair attempts'):self.call()
        self.assertEqual(request.call_count,3);self.assertFalse((self.ai.runtime/'cache').exists())
        self.assertEqual(self.sleeps,[])
    def test_200_missing_fields_is_also_repaired(self):
        with patch('urllib.request.urlopen',side_effect=[Response(FAILED),Response(corrected())]) as request:
            self.assertEqual(self.call(),corrected())
        self.assertEqual(request.call_count,2)
    def test_repair_response_still_must_satisfy_original_schema(self):
        bad=corrected();bad['events'][0]['unexpected']='bad'
        with patch('urllib.request.urlopen',side_effect=[rejected(),Response(bad),Response(corrected())]) as request:
            self.assertEqual(self.call(),corrected())
        self.assertEqual(request.call_count,3)
        last=json.loads(request.call_args_list[-1].args[0].data)
        self.assertIn('unexpected fields unexpected',last['messages'][-1]['content'])
        self.assertEqual(len(last['messages']),3) # bounded feedback, not growing conversation
    def test_other_400_is_not_retried_or_hidden(self):
        error={'error':{'code':'model_not_found','message':'Unknown model.'}}
        with patch('urllib.request.urlopen',side_effect=rejected(error=error)) as request:
            with self.assertRaisesRegex(AIUnavailable,'Unknown model'):self.call()
        self.assertEqual(request.call_count,1)
    def test_authentication_error_is_not_retried(self):
        with patch('urllib.request.urlopen',side_effect=rejected(code=401,error={'error':{'message':'Invalid key.'}})) as request:
            with self.assertRaisesRegex(AIUnavailable,'401'):self.call()
        self.assertEqual(request.call_count,1)
    def test_schema_repair_preserves_quota_wait_and_retry(self):
        h={'x-ratelimit-remaining-tokens':'1','x-ratelimit-limit-tokens':'8000','x-ratelimit-reset-tokens':'2.5s'}
        quota=rejected(code=429,error={'error':{'message':'Quota.'}},headers={'Retry-After':'0.25'})
        with patch('urllib.request.urlopen',side_effect=[rejected(headers=h),quota,Response(corrected())]) as request:
            self.assertEqual(self.call(),corrected())
        self.assertEqual(request.call_count,3);self.assertAlmostEqual(sum(self.sleeps),.25)
    def test_invalid_cached_output_is_discarded_and_regenerated(self):
        with patch('urllib.request.urlopen',return_value=Response(corrected())):self.call()
        cached=next((self.ai.runtime/'cache').glob('*.json'))
        raw=json.loads(cached.read_text());raw['choices'][0]['message']['content']=json.dumps(FAILED)
        cached.write_text(json.dumps(raw))
        with patch('urllib.request.urlopen',return_value=Response(corrected())) as request:self.call()
        self.assertEqual(request.call_count,1)
        self.assertIn('invalid_cache_discarded',(self.ai.runtime/'logs/ai-responses.jsonl').read_text())
    def test_nested_types_and_unknown_fields_remain_strict(self):
        for mutate in [lambda x:x['events'][0]['ranges'][0].update(first=True),
                       lambda x:x['events'][0].update(category='made_up'),
                       lambda x:x['events'][0].update(threads='not an array'),
                       lambda x:x.update(extra=1)]:
            answer=corrected();mutate(answer)
            with self.assertRaises(ValueError):Nova.validate_schema(answer,schemas.ANALYSIS)
    def test_valid_cache_still_skips_network(self):
        with patch('urllib.request.urlopen',return_value=Response(corrected())):self.call()
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network expected')):
            self.assertEqual(self.call(),corrected())
    def test_same_validator_handles_review_profile_and_queue_schemas(self):
        for schema,value in [(schemas.REVIEW,dict(events=[],unsupported_coverage=[])),
                             (schemas.PROFILES,dict(characters=[])),
                             (schemas.QUEUE,dict(explanation='No supported event',items=[]))]:
            self.assertEqual(Nova.validate_schema(value,schema),value)
        with self.assertRaises(ValueError):
            Nova.validate_schema(dict(events=[dict(index=0,supported=1,reason='wrong boolean',unsupported_relationships=[])],unsupported_coverage=[]),schemas.REVIEW)

if __name__=='__main__':unittest.main()
