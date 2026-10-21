"""Injected AI/HTTP fixtures. These are regressions, not live-provider results."""
import copy, io, json, os, tempfile, unittest, urllib.error
from unittest.mock import patch
from backend.ai import Nova, AIUnavailable
from backend.pipeline import Pipeline

class ClaimAI:
    key='test';model='fixture'
    def __init__(self,relationships_only=False,broken=False):
        self.calls=[];self.logs=[];self.relationships_only=relationships_only;self.broken=broken
    def log(self,record):self.logs.append(record)
    def invalidate(self,task=None):pass
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append((task,copy.deepcopy(data)))
        if task=='extract':
            events=[]
            for cue,title in ((34,'A stated plan'),(35,'Memory overwrite in progress'),(50,'Crew aborts operation')):
                events.append(dict(title=title,summary=title,category='critical',why='Changes their plan.',
                    characters=['captain','tech_head'],threads=['plan'],location='Not stated',
                    evidence=next(c['text'] for c in data['cues'] if c['id']==cue),ranges=[dict(first=cue,last=cue)],
                    relationships=[dict(source='captain',target='tech_head',label='commands',explanation='Unsupported command relationship.')]))
            if self.relationships_only:events=events[:1]
            return dict(events=events,coverage=[dict(first=34,last=50,category='critical',why='Technical operation')])
        if task=='review':
            if self.broken:return dict(events=[],unsupported_coverage=[])
            reasons=['The plan is explicitly stated.',"Cue 35 says You're a jerk, Thom!, not a memory overwrite.", 'Cue 50 says Cut!, which does not establish an abort decision.']
            return dict(events=[dict(index=e['index'],supported=e['index']==0,reason=reasons[e['index']],unsupported_relationships=[0]) for e in data['events']],unsupported_coverage=[0])
        raise AssertionError(task)

class ClaimTests(unittest.TestCase):
    def group(self):
        return [dict(id=i,start=float(i),end=i+.7,text={34:'We will meet tomorrow.',35:"You're a jerk, Thom!",50:'Cut!'}.get(i,'Background dialogue.')) for i in range(34,51)]
    def run_extract(self,ai):
        group=self.group()
        with tempfile.TemporaryDirectory() as rt:
            return Pipeline(None,ai,rt).extract_reviewed(dict(cast=[dict(id='captain'),dict(id='tech_head')],cues=group),group,{'captain','tech_head'},0,lambda *args:None)
    def test_reported_bad_events_do_not_block_verified_event(self):
        ai=ClaimAI();events,spans=self.run_extract(ai)
        self.assertEqual([e['title'] for e in events],['A stated plan'])
        self.assertEqual(events[0]['relationships'],[])
        self.assertEqual(events[0]['evidence_cue_ids'],[34])
        self.assertTrue(all(s['category']=='unclassified' for s in spans))
        self.assertEqual([c[0] for c in ai.calls],['extract','review','extract','review'])
        # Reviewer gets the actual selected text, not just a quote/assertion.
        self.assertEqual(ai.calls[1][1]['events'][1]['selected_cues'][0]['text'],"You're a jerk, Thom!")
        self.assertIn('Cue 35',ai.calls[2][1]['repair_feedback'][0])
    def test_bad_relationship_removed_without_repeating_extraction(self):
        ai=ClaimAI(relationships_only=True);events,_=self.run_extract(ai)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['relationships'],[])
        self.assertEqual(len(ai.calls),2)
        self.assertTrue(any(x['status']=='relationships_omitted' for x in ai.logs))
    def test_missing_review_verdict_is_not_treated_as_approval(self):
        events,spans=self.run_extract(ClaimAI(broken=True))
        self.assertEqual(events,[])
        self.assertTrue(any(s['category']=='unclassified' for s in spans))
    def test_out_of_range_review_index_is_rejected(self):
        ai=ClaimAI()
        with tempfile.TemporaryDirectory() as rt:
            p=Pipeline(None,ai,rt)
            with self.assertRaisesRegex(ValueError,'review index'):
                p.apply_review(dict(events=[dict(index=99,supported=True,reason='x',unsupported_relationships=[])],unsupported_coverage=[]),[dict(relationships=[])],[],[],self.group())

class Response(io.BytesIO):
    status=200
    def __init__(self,headers=None):
        raw=dict(choices=[dict(finish_reason='stop',message=dict(content='{"ok":true}'))])
        super().__init__(json.dumps(raw).encode());self.headers=headers or {}

class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ai=Nova(self.tmp.name,key='test-key',interval=0,model='nova-2-lite-v1')
        self.now=1000.;self.sleeps=[]
        self.clock=patch('backend.ai.time.monotonic',side_effect=lambda:self.now);self.clock.start()
        self.sleep=patch('backend.ai.time.sleep',side_effect=self.advance);self.sleep.start()
    def tearDown(self):self.sleep.stop();self.clock.stop();self.tmp.cleanup()
    def advance(self,seconds):self.sleeps.append(seconds);self.now+=seconds
    def call(self,task='test'):return self.ai.json(task,'Short instruction',{},dict(type='object'),max_tokens=100)
    def test_legacy_one_minute_interval_is_ignored(self):
        with patch.dict(os.environ,{'AI_MIN_INTERVAL':'65','AI_REQUEST_INTERVAL':'0'}):
            self.assertEqual(Nova(self.tmp.name,key='test',model='nova-2-lite-v1').interval,0)
    def test_two_calls_without_quota_pressure_have_no_forced_delay(self):
        with patch('urllib.request.urlopen',side_effect=[Response(),Response()]):self.call('one');self.call('two')
        self.assertEqual(self.sleeps,[])
    def test_unknown_token_counters_do_not_infer_waits(self):
        h={'x-ratelimit-remaining-tokens':'10','x-ratelimit-limit-tokens':'8000','x-ratelimit-reset-tokens':'7.66s'}
        with patch('urllib.request.urlopen',side_effect=[Response(h),Response()]):self.call('one');self.call('two')
        self.assertEqual(self.sleeps,[])
    def test_daily_request_counter_is_not_rpm(self):
        h={'x-ratelimit-remaining-requests':'1','x-ratelimit-reset-requests':'23h'}
        with patch('urllib.request.urlopen',side_effect=[Response(h),Response()]):self.call('one');self.call('two')
        self.assertEqual(self.sleeps,[])
    def test_undocumented_request_counters_do_not_block_nova(self):
        self.ai.update_quota({'x-ratelimit-remaining-requests':'0','x-ratelimit-reset-requests':'23h'})
        with patch('urllib.request.urlopen',return_value=Response()):self.assertTrue(self.call()['ok'])
        self.assertEqual(self.sleeps,[])
    def test_429_retries_after_fractional_retry_after(self):
        err=urllib.error.HTTPError('https://api.nova.amazon.com/',429,'quota',{'Retry-After':'0.25'},io.BytesIO(b'quota'))
        with patch('urllib.request.urlopen',side_effect=[err,Response()]) as request:
            self.assertTrue(self.call()['ok']);self.assertEqual(request.call_count,2)
        self.assertAlmostEqual(sum(self.sleeps),.25)
    def test_429_exhaustion_is_bounded_and_logged(self):
        def error(*args,**kwargs):raise urllib.error.HTTPError('https://api.nova.amazon.com/',429,'quota',{'Retry-After':'0.1'},io.BytesIO(b'quota'))
        with patch('urllib.request.urlopen',side_effect=error) as request:
            with self.assertRaises(AIUnavailable):self.call()
        self.assertEqual(request.call_count,3);self.assertAlmostEqual(sum(self.sleeps),.2)
        log=(self.ai.runtime/'logs/ai-responses.jsonl').read_text()
        self.assertEqual(log.count('"status": "quota_retry"'),2)
    def test_success_logs_quota_headers_but_not_auth(self):
        with patch('urllib.request.urlopen',return_value=Response({'Authorization':'secret-other','x-ratelimit-remaining-tokens':'7777'})):self.call()
        log=(self.ai.runtime/'logs/ai-responses.jsonl').read_text()
        self.assertIn('7777',log);self.assertNotIn('secret-other',log);self.assertNotIn('test-key',log)
    def test_cache_avoids_network_and_quota_wait(self):
        with patch('urllib.request.urlopen',return_value=Response()):self.call()
        self.ai.update_quota({'x-ratelimit-remaining-requests':'0','x-ratelimit-reset-requests':'23h'})
        with patch('urllib.request.urlopen',side_effect=AssertionError('Cache miss')):self.assertTrue(self.call()['ok'])
        self.assertEqual(self.sleeps,[])
    def test_retry_parsing(self):
        self.assertEqual(Nova.retry_seconds('invalid',3),3)
        self.assertEqual(Nova.retry_seconds('.25'),.25)

if __name__=='__main__':unittest.main()
