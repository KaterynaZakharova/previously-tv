"""Transport and recovery tests only; successful model outputs are injected fixtures."""
import copy,io,json,os,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from backend.ai import Nova,AIUnavailable,AIResponseInvalid,make_ai
from backend.pipeline import Pipeline,chunks
from backend import schemas

FIXTURE=json.loads((Path(__file__).parent/'fixtures/sparse_coverage_failure.json').read_text())
SCHEMA=schemas.obj(ok=dict(type='boolean'),message=schemas.S)
class HTTPResponse(io.BytesIO):
    status=200
    def __init__(self,data,headers=None):super().__init__(json.dumps(data).encode());self.headers=headers or {}
def reply(value,finish='tool_calls',name='emit_result'):
    return dict(choices=[dict(finish_reason=finish,message=dict(content=None,tool_calls=[dict(type='function',function=dict(name=name,arguments=json.dumps(value)))]))])
class NovaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.ai=Nova(self.root,key='secret-nova-test',model='nova-lite-v2',interval=0)
        self.now=1000.;self.sleeps=[]
        self.clock=patch('backend.ai.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        def advance(seconds):self.sleeps.append(seconds);self.now+=seconds
        self.sleep=patch('backend.ai.time.sleep',side_effect=advance);self.sleep.start()
    def tearDown(self):self.sleep.stop();self.clock.stop();self.tmp.cleanup()
    def call(self):return self.ai.json('test','Return the requested object.',{},SCHEMA,max_tokens=100)
    def test_default_factory_selects_nova(self):
        with patch.dict(os.environ,{},clear=True):
            ai=make_ai(self.root);self.assertIsInstance(ai,Nova);self.assertEqual(ai.key,'');self.assertEqual(ai.model,'auto')
    def test_factory_uses_nova_configuration(self):
        with patch.dict(os.environ,{'NOVA_API_KEY':'fixture-only','NOVA_MODEL':'nova-pro-v1'},clear=True):
            ai=make_ai(self.root);self.assertIsInstance(ai,Nova);self.assertEqual(ai.key,'fixture-only');self.assertEqual(ai.model,'nova-pro-v1')
    def test_nova_wire_format_endpoint_and_tool_result(self):
        expected=dict(ok=True,message='Fixture only')
        with patch('urllib.request.urlopen',return_value=HTTPResponse(reply(expected))) as request:self.assertEqual(self.call(),expected)
        sent=request.call_args.args[0];payload=json.loads(sent.data)
        self.assertEqual(sent.full_url,'https://api.nova.amazon.com/v1/chat/completions')
        self.assertEqual(payload['tool_choice'],'required');self.assertEqual(payload['tools'][0]['function']['parameters'],SCHEMA)
        self.assertNotIn('response_format',payload);self.assertNotIn('reasoning_effort',payload)
        self.assertEqual(payload['model'],'nova-lite-v2');self.assertEqual(payload['max_completion_tokens'],100)
        log=(self.root/'logs/ai-responses.jsonl').read_text();self.assertIn('"provider": "nova"',log);self.assertNotIn('secret-nova-test',log)
    def test_auto_discovers_only_models_actually_available(self):
        self.ai.model='auto'
        with patch('urllib.request.urlopen',side_effect=[HTTPResponse(dict(data=[dict(id='nova-2-lite-v1'),dict(id='nova-pro-v1')])),HTTPResponse(reply(dict(ok=True,message='test')))]) as request:self.call()
        self.assertEqual(self.ai.model,'nova-2-lite-v1');self.assertEqual(request.call_args_list[0].args[0].full_url,'https://api.nova.amazon.com/v1/models')
        self.assertEqual(json.loads(request.call_args_list[1].args[0].data)['model'],'nova-2-lite-v1')
    def test_unknown_model_list_produces_explicit_configuration_error(self):
        self.ai.model='auto'
        with patch('urllib.request.urlopen',return_value=HTTPResponse(dict(data=[dict(id='different-model')]))):
            with self.assertRaisesRegex(AIUnavailable,'Set NOVA_MODEL'):self.call()
    def test_missing_key_never_contacts_api(self):
        self.ai.key=''
        with patch('urllib.request.urlopen',side_effect=AssertionError('No network without key')):
            with self.assertRaisesRegex(AIUnavailable,'NOVA_API_KEY'):self.call()
    def test_nova_structural_repair_keeps_tool_transport(self):
        with patch('urllib.request.urlopen',side_effect=[HTTPResponse(reply(dict(message='missing ok'))),HTTPResponse(reply(dict(ok=True,message='repaired')))]) as request:
            self.assertTrue(self.call()['ok'])
        payload=json.loads(request.call_args_list[1].args[0].data)
        self.assertIn('tools',payload);self.assertNotIn('response_format',payload);self.assertIn('missing required fields ok',payload['messages'][-1]['content'])
        self.assertIn('function_tool',(self.root/'logs/ai-responses.jsonl').read_text())
    def test_wrong_tool_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError,'Unexpected Nova'):Nova.checked_parse(reply(dict(ok=True,message='test'),name='run_shell'),SCHEMA)
    def test_plain_json_compatible_response_is_still_validated(self):
        raw=dict(choices=[dict(finish_reason='stop',message=dict(content='{"ok":true,"message":"test"}'))])
        self.assertTrue(Nova.checked_parse(raw,SCHEMA)['ok'])
        raw['choices'][0]['message']['content']='{"message":"missing"}'
        with self.assertRaises(ValueError):Nova.checked_parse(raw,SCHEMA)
    def test_incomplete_generation_never_becomes_a_result(self):
        with self.assertRaisesRegex(ValueError,'Incomplete Nova'):Nova.checked_parse(reply(dict(ok=True,message='test'),finish='length'),SCHEMA)
    def test_cache_skips_network_and_uses_validated_tool_arguments(self):
        with patch('urllib.request.urlopen',return_value=HTTPResponse(reply(dict(ok=True,message='test')))):self.call()
        with patch('urllib.request.urlopen',side_effect=AssertionError('Unexpected network')):self.assertTrue(self.call()['ok'])
    def test_nova_does_not_apply_undocumented_counter_semantics(self):
        self.ai.update_quota({'x-ratelimit-remaining-requests':'0','x-ratelimit-reset-requests':'24h'})
        with patch('urllib.request.urlopen',return_value=HTTPResponse(reply(dict(ok=True,message='test')))):self.call()
        self.assertEqual(self.sleeps,[])
    def test_nova_429_retries_with_retry_after(self):
        err=urllib.error.HTTPError(self.ai.endpoint,429,'quota',{'Retry-After':'0.4'},io.BytesIO(b'{"error":{"message":"quota"}}'))
        with patch('urllib.request.urlopen',side_effect=[err,HTTPResponse(reply(dict(ok=True,message='test')))]):self.call()
        self.assertAlmostEqual(sum(self.sleeps),.4)
    def test_nova_authentication_error_does_not_retry_other_providers(self):
        err=urllib.error.HTTPError(self.ai.endpoint,401,'auth',{},io.BytesIO(b'{"error":{"message":"Invalid key"}}'))
        with patch('urllib.request.urlopen',side_effect=err) as request:
            with self.assertRaisesRegex(AIUnavailable,'Amazon Nova HTTP 401'):self.call()
        self.assertEqual(request.call_count,1)
    def test_nova_temporary_server_error_retries_without_changing_provider(self):
        err=urllib.error.HTTPError(self.ai.endpoint,500,'temporary',{},io.BytesIO(b'{}'))
        with patch('urllib.request.urlopen',side_effect=[err,HTTPResponse(reply(dict(ok=True,message='recovered')))]):
            self.assertTrue(self.call()['ok'])
        self.assertIn('transient_retry',(self.root/'logs/ai-responses.jsonl').read_text())
    def test_nova_interrupted_connection_retries_with_the_same_payload(self):
        with patch('urllib.request.urlopen',side_effect=[urllib.error.URLError('Connection interrupted'),HTTPResponse(reply(dict(ok=True,message='recovered')))]) as request:
            self.assertTrue(self.call()['ok'])
        self.assertEqual(request.call_args_list[0].args[0].data,request.call_args_list[1].args[0].data)
        self.assertIn('network_retry',(self.root/'logs/ai-responses.jsonl').read_text())

class SparseAI:
    key='test';model='fixture'
    def __init__(self,mode='normal'):self.logs=[];self.calls=[];self.mode=mode
    def log(self,x):self.logs.append(x)
    def invalidate(self,task=None):pass
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append((task,data))
        if task=='review':return dict(events=[dict(index=x['index'],supported=True,reason='Fictional test verdict only',unsupported_relationships=[]) for x in data['events']],unsupported_coverage=[])
        if task=='coverage_repair':
            if self.mode=='unavailable':raise AIUnavailable('Fixture unavailable')
            lookup={c['id']:c for c in data['cues']}
            return dict(classifications=[dict(cue_id=i,category='secondary',why='Fictional fixture context.',evidence=lookup[i]['text'] if self.mode!='bad_quote' else 'fabricated words') for i in data['cue_ids']])
        if task=='extract':
            e=dict(title='Overwrite progress reported',summary='Dialogue reports an overwrite in progress.',category='critical',why='States progress.',
                   characters=[],threads=['progress'],location='Not stated',evidence='Memory overwrite in progress!',ranges=[dict(first=34,last=34)],relationships=[])
            return dict(events=[e],coverage=[dict(first=34,last=34,category='critical',why='States progress.')])
        raise AssertionError(task)
class SparseCoverageTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.ai=SparseAI();self.p=Pipeline(None,self.ai,self.tmp.name);self.group=FIXTURE['cues'];self.cast=set(FIXTURE['cast_ids'])
    def tearDown(self):self.tmp.cleanup()
    def test_logged_missing_tail_is_retained_as_unclassified_not_fatal(self):
        events,spans,rows,issues=self.p.prepare_chunk(FIXTURE['attempts'][0],self.group,self.cast,1)
        self.assertEqual(len(events),4);self.assertEqual(issues,[])
        missing=[s['cue_id'] for s in spans if s['category']=='unclassified']
        self.assertEqual(missing,[74,75,76]);self.assertEqual(rows[-1]['last'],73)
    def test_logged_nonpartitioning_repair_no_longer_blocks_event_review(self):
        events,spans,rows,issues=self.p.prepare_chunk(FIXTURE['attempts'][1],self.group,self.cast,1)
        self.assertEqual(len(events),4);self.assertEqual(issues,[])
        self.assertTrue(any(s['cue_id']==37 and s['category']=='unclassified' for s in spans))
        self.assertEqual(rows[-1]['last'],40)
    def test_sparse_coverage_pipeline_still_reviews_and_keeps_supported_event(self):
        events,spans=self.p.extract_reviewed(dict(cast=[],cues=self.group),self.group,self.cast,1,lambda *args:None)
        self.assertEqual(len(events),1);self.assertEqual([x[0] for x in self.ai.calls],['extract','review'])
        self.assertTrue(any(s['category']=='unclassified' for s in spans))
    def test_coverage_conflicts_are_unclassified_instead_of_arbitrarily_resolved(self):
        group=[dict(id=i,start=i*2.,end=i*2.+1,text=str(i)) for i in range(1,5)]
        rows,spans=self.p.sparse_coverage([dict(first=1,last=3,category='critical',why='A'),dict(first=2,last=4,category='filler',why='B')],group)
        self.assertEqual({s['cue_id']:s['category'] for s in spans if 'cue_id' in s},{1:'critical',2:'unclassified',3:'unclassified',4:'filler'})
    def test_out_of_range_coverage_is_skipped_without_invented_labels(self):
        rows,spans=self.p.sparse_coverage([dict(first=0,last=999,category='critical',why='bad')],self.group)
        self.assertEqual(rows,[]);self.assertTrue(all(s['category']=='unclassified' for s in spans))
    def test_relationship_outside_event_cast_is_removed_independently(self):
        answer=copy.deepcopy(FIXTURE['attempts'][0]);answer['events'][0]['relationships']=[dict(source='thom',target='celia',label='bad',explanation='not supported by this cast')]
        events,_,_,issues=self.p.prepare_chunk(answer,self.group,self.cast,1)
        self.assertEqual(len(events),4);self.assertEqual(events[0]['relationships'],[]);self.assertEqual(issues,[])
    def test_supplementary_pass_can_fill_real_cue_labels(self):
        _,spans=self.p.sparse_coverage([],self.group[:2]);filled=self.p.complete_dialogue_coverage(spans,self.group,[],lambda *args:None)
        self.assertTrue(all(s['category']=='secondary' for s in filled));self.assertTrue(all(s['basis']=='dialogue' for s in filled))
    def test_supplementary_pass_failure_keeps_unknown_labels(self):
        for mode in ('unavailable','bad_quote'):
            ai=SparseAI(mode);p=Pipeline(None,ai,self.tmp.name);_,spans=p.sparse_coverage([],self.group[:2])
            self.assertTrue(all(s['category']=='unclassified' for s in p.complete_dialogue_coverage(spans,self.group,[],lambda *args:None)))
    def test_missing_review_does_not_discard_independently_approved_events(self):
        events=[dict(id='a',start=0,relationships=[],ranges=[dict(start=0,end=1)]),dict(id='b',start=2,relationships=[],ranges=[dict(start=2,end=3)])]
        review=dict(events=[dict(index=0,supported=True,reason='fixture',unsupported_relationships=[])],unsupported_coverage=[])
        accepted,_,issues=self.p.apply_review(review,events,[dict(start=0,end=3,category='critical',basis='dialogue')],[],self.group)
        self.assertEqual([e['id'] for e in accepted],['a']);self.assertIn('no supporting review',issues[0])
    def test_full_short_movie_fits_nova_passage_policy(self):
        self.assertEqual(len(list(chunks(self.group,limit=24000,max_span=1800))),1)
    def test_coverage_enum_restricts_supplementary_ids(self):
        with self.assertRaisesRegex(ValueError,'invalid enum'):
            Nova.validate_schema(dict(classifications=[dict(cue_id=999,category='secondary',why='bad',evidence='bad')]),schemas.coverage_for_cues([74,75,76]))

if __name__=='__main__':unittest.main()
