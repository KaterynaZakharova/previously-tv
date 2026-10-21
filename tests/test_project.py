"""Injected fictional AI fixtures ONLY in tests; never a production fallback."""
import copy,io,json,tempfile,threading,time,unittest,subprocess,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
from unittest.mock import patch
from backend.catalog import Catalog,read_srt,inside
from backend.pipeline import Pipeline,ranges_to_edl
from backend.service import Service,initial_state
from backend.ai import Nova,AIUnavailable
from server import create_server

class FixtureAI:
    key='test-only';model='fixture-not-live'
    def __init__(self):self.calls=[];self.logs=[]
    def log(self,x):self.logs.append(x)
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append((task,copy.deepcopy(data)))
        if task=='extract':
            events=[]
            for j,(a,b,people) in enumerate(((1,2,['alice','bob']),(3,4,['bob']),(5,6,['alice']))):
                events.append(dict(title='Evidence '+str(j),summary='Test story fact '+str(j),category='critical' if j<2 else 'secondary',why='Changes the test story.',characters=people,threads=['test_clue'],location='Test room',evidence=data['cues'][a-1]['text'],ranges=[dict(first=a,last=b)],relationships=[dict(source='alice',target='bob',label='allies',explanation='They share a clue.')] if j==0 else []))
            return dict(events=events,coverage=[dict(first=1,last=4,category='critical',why='Test clue'),dict(first=5,last=6,category='secondary',why='Test context')])
        if task=='review':return dict(events=[dict(index=e['index'],supported=True,reason='Supported by fixture dialogue.',unsupported_relationships=[]) for e in data['events']],unsupported_coverage=[])
        if task=='profiles':return dict(characters=[dict(id=c['id'],description='Test description',motivation='Resolve the clue') for c in data['cast']])
        if task=='scene_coverage':return dict(scenes=[dict(index=r['index'],category='secondary',title='Fictional test scene',summary='Fixture only',why='Fixture context',characters=['alice','bob'],visual_evidence='Fictional injected frame assessment',evidence_cue_ids=[c['id'] for c in r['cues']]) for r in data['intervals']])
        return dict(explanation='Test fixture only.',items=[dict(event_id=e['id'],reason='Test AI reason',ranges=[dict(first=r['first'],last=r['last']) for r in e.get('context_ranges',e['ranges'])]) for e in data['candidates'][:2]])

class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);m=cls.root/'assets/inputs/test_story';m.mkdir(parents=True)
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=160x90:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','16','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(m/'movie.mp4')],check=True)
        (m/'transcript.srt').write_text('\n\n'.join(f'{i}\n00:00:{(i-1)*2+1:02d},000 --> 00:00:{i*2:02d},000\nTest line {i}.' for i in range(1,7)))
        for n in ('alice','bob'):(m/(n+'.jpg')).write_bytes(b'fixture')
        (m/'movie.json').write_text(json.dumps(dict(id='test_story',title='Test story',year=2026,video='movie.mp4',transcript='transcript.srt',characters=[dict(id=n,name=n.title(),photo=n+'.jpg') for n in ('alice','bob')],license={'name':'Test fixture'})))
        (cls.root/'frontend').mkdir();(cls.root/'frontend/index.html').write_text('frontend fixture')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.rt=tempfile.TemporaryDirectory();self.runtime=Path(self.rt.name);self.ai=FixtureAI();self.cat=Catalog(self.root);self.pipe=Pipeline(self.cat,self.ai,self.runtime)
    def tearDown(self):self.rt.cleanup()
    def analyze(self):return self.pipe.analyze(self.cat.get('test_story'),lambda *x:None)
    def test_dynamic_analysis_cache_graph_and_timeline(self):
        a,folder=self.analyze();self.assertEqual(len(a['events']),3);self.assertEqual(len(a['characters']),2);self.assertEqual(a['events'][0]['relationships'][0]['label'],'allies')
        self.assertEqual(a['timeline'][0]['start'],0);self.assertAlmostEqual(a['timeline'][-1]['end'],a['duration']);self.assertEqual(a['timeline'][0]['category'],'unclassified')
        for x,y in zip(a['timeline'],a['timeline'][1:]):self.assertEqual(x['end'],y['start'])
        count=len(self.ai.calls);self.analyze();self.assertEqual(count,len(self.ai.calls))
        p=self.root/'assets/inputs/test_story/transcript.srt';original=p.read_text();p.write_text(original+'\n');self.assertNotEqual(self.cat.fingerprint(self.cat.get('test_story')),a['fingerprint']);p.write_text(original)
    def test_nova_policy_uses_wider_story_context(self):
        self.ai.provider='nova';self.ai.label='Amazon Nova fixture'
        self.analyze()
        extract=[data for task,data in self.ai.calls if task=='extract']
        self.assertEqual(len(extract),1);self.assertEqual(extract[0]['max_events'],10)
        self.assertTrue(all('start_timecode' in c for c in extract[0]['cues']))
    def test_sparse_coverage_end_to_end_still_builds_clips(self):
        class SparseFixture(FixtureAI):
            def json(self,task,instruction,data,schema,progress,max_tokens):
                if task=='coverage_repair':
                    self.calls.append((task,copy.deepcopy(data)))
                    lookup={c['id']:c for c in data['cues']}
                    return dict(classifications=[dict(cue_id=i,category='secondary',why='Fictional fixture context.',evidence=lookup[i]['text']) for i in data['cue_ids']])
                result=super().json(task,instruction,data,schema,progress,max_tokens)
                if task=='extract':result['coverage']=result['coverage'][:1]
                return result
        self.ai=SparseFixture();self.pipe=Pipeline(self.cat,self.ai,self.runtime)
        analysis,folder=self.analyze();queue=self.pipe.recommend(self.cat.get('test_story'),analysis,folder,initial_state(),lambda *args:None)
        self.assertEqual(len(analysis['events']),3);self.assertTrue(queue['items'])
        self.assertTrue(any(task=='coverage_repair' for task,_ in self.ai.calls))
    def test_precise_montage_with_real_ffmpeg_and_audio(self):
        a,folder=self.analyze();cues=read_srt(self.cat.inputs(self.cat.get('test_story'))[1]);edl=ranges_to_edl([dict(first=1,last=1),dict(first=3,last=3)],cues)
        url=self.pipe.render(self.cat.inputs(self.cat.get('test_story'))[0],folder,edl);file=self.runtime/'artifacts'/url[len('/generated/'):]
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(file)]))
        self.assertAlmostEqual(float(info['format']['duration']),sum(r['end']-r['start'] for r in edl),delta=.22);self.assertEqual({s['codec_type'] for s in info['streams']},{'video','audio'})
    def test_evidence_and_refinement_guards(self):
        cues=read_srt(self.cat.inputs(self.cat.get('test_story'))[1])
        for ranges in ([dict(first=999,last=999)],[dict(first=3,last=2)],[dict(first=3,last=4),dict(first=1,last=2)]):
            with self.assertRaises(ValueError):ranges_to_edl(ranges,cues)
        with self.assertRaises(ValueError):ranges_to_edl([dict(first=1,last=4)],cues,[dict(first=1,last=2)])
        ans=self.ai.json('extract','',dict(cues=cues),{},lambda *x:None,1000);ans['events'][0]['evidence']='Invented quote'
        with self.assertRaises(ValueError):self.pipe.validate_chunk(ans,cues,{'alice','bob'},0)
        ans=self.ai.json('extract','',dict(cues=cues),{},lambda *x:None,1000);ans['coverage'][0]['first']=2
        with self.assertRaises(ValueError):self.pipe.validate_chunk(ans,cues,{'alice','bob'},0)
    def test_ai_queue_respects_both_feedbacks_character_and_pair(self):
        a,folder=self.analyze();s=initial_state();q=self.pipe.recommend(self.cat.get('test_story'),a,folder,s,lambda *x:None);first=q['items'][0]['event_id']
        for memory in (0,1):
            s['memory'][first]=memory;s['last_rated']=first;after=self.pipe.recommend(self.cat.get('test_story'),a,folder,s,lambda *x:None)
            self.assertNotEqual(q['items'],after['items']);self.assertNotIn(first,[i['event_id'] for i in after['items']])
        s=initial_state();s['character']='alice';q=self.pipe.recommend(self.cat.get('test_story'),a,folder,s,lambda *x:None)
        for i in q['items']:self.assertIn('alice',next(e for e in a['events'] if e['id']==i['event_id'])['characters'])
        s['pair']=['alice','bob'];s['character']=None;q=self.pipe.recommend(self.cat.get('test_story'),a,folder,s,lambda *x:None);self.assertEqual(len(q['items']),1)
    def test_no_key_no_placeholder(self):
        service=Service(self.root,self.runtime,Nova(self.runtime,key='',interval=0,model='nova-2-lite-v1'));job=service.start('viewer','test_story',{'type':'build'})
        for _ in range(100):
            j=service.job('viewer','test_story',job['id'])
            if j['status']!='running':break
            time.sleep(.01)
        self.assertEqual(j['status'],'unavailable');self.assertIsNone(j['queue']);self.assertIsNone(j['analysis']);self.assertIn('AI unavailable',j['error'])
    def test_no_verified_events_is_analysis_failure_not_provider_outage(self):
        class RejectAI(FixtureAI):
            def json(self,task,instruction,data,schema,progress,max_tokens):
                if task=='review':
                    return dict(events=[dict(index=e['index'],supported=False,reason='Unsupported fixture fact',unsupported_relationships=[]) for e in data['events']],unsupported_coverage=[0,1])
                return super().json(task,instruction,data,schema,progress,max_tokens)
        service=Service(self.root,self.runtime,RejectAI());job=service.start('viewer','test_story',{'type':'build'})
        for _ in range(200):
            j=service.job('viewer','test_story',job['id'])
            if j['status']!='running':break
            time.sleep(.01)
        self.assertEqual(j['status'],'unavailable');self.assertEqual(j['error_type'],'analysis')
        self.assertIn('no recap events',j['error']);self.assertNotIn('AI unavailable',j['error'])
        self.assertIsNone(j['queue']);self.assertIsNone(j['analysis'])
    def test_all_raw_responses_cache_errors_key_redaction(self):
        ai=Nova(self.runtime,key='secret-test-key',interval=0,model='nova-2-lite-v1');raw=dict(choices=[dict(finish_reason='stop',message=dict(content='{"ok":true}',reasoning='provider reasoning field'))])
        class Response(io.BytesIO):status=200
        with patch('urllib.request.urlopen',return_value=Response(json.dumps(raw).encode())):self.assertTrue(ai.json('test','instruction',{},dict(type='object'),max_tokens=100)['ok'])
        with patch('urllib.request.urlopen',side_effect=AssertionError('Cache must avoid network')):ai.json('test','instruction',{},dict(type='object'),max_tokens=100)
        ai.max_retries=0
        error=urllib.error.HTTPError('https://api.nova.amazon.com/',429,'quota',{},io.BytesIO(b'{"error":"secret-test-key quota"}'))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(AIUnavailable):ai.json('other','instruction',{},dict(type='object'),max_tokens=100)
        text=(self.runtime/'logs/ai-responses.jsonl').read_text();self.assertIn('provider reasoning field',text);self.assertIn('cache_hit',text);self.assertIn('quota',text);self.assertNotIn('secret-test-key',text)
    def test_http_jobs_sessions_ranges_and_private_files(self):
        server=create_server(port=0,root=self.root,runtime=self.runtime,ai=self.ai);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            def get(path):return json.load(client.open(base+path))
            self.assertTrue(get('/api/library')['movies'][0]['inputs_ready']);job=json.load(client.open(urllib.request.Request(base+'/api/movie/test_story/action',data=b'{"type":"build"}')))
            for _ in range(400):
                result=get('/api/movie/test_story/jobs/'+job['id'])
                if result['status']!='running':break
                time.sleep(.025)
            self.assertEqual(result['status'],'ready');self.assertTrue(result['queue']['items'])
            with urllib.request.urlopen(urllib.request.Request(base+result['queue']['items'][0]['clip'],headers={'Range':'bytes=0-15'})) as r:self.assertEqual(r.status,206);self.assertEqual(len(r.read()),16)
            for path in ('/.env','/../server.py','/generated/../logs/ai-responses.jsonl','/inputs/test_story/movie.json'):
                with self.assertRaises(urllib.error.HTTPError) as e:client.open(base+path)
                self.assertEqual(e.exception.code,404)
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/movie/test_story/jobs/'+job['id'])
            with self.assertRaises(urllib.error.HTTPError) as e:client.open(urllib.request.Request(base+'/api/movie/test_story/action',data=b'{}',headers={'Origin':'http://evil.invalid'}))
            self.assertEqual(e.exception.code,403)
            eid=result['queue']['items'][0]['event_id'];client.open(urllib.request.Request(base+'/api/movie/test_story/action',data=json.dumps(dict(type='remember',event_id=eid)).encode())).read()
            self.assertEqual(get('/api/movie/test_story')['state']['memory'][eid],1)
            # Let the daemon job finish before temporary directory cleanup.
            while server.service.jobs[server.service.active[(next(iter(server.service.active))[0],'test_story')]]['status']=='running':time.sleep(.02)
        finally:server.shutdown();server.server_close();thread.join()
    def test_safe_paths_and_generic_catalog(self):
        with self.assertRaises(ValueError):inside(self.root/'assets/inputs/test_story','../../server.py')
        self.assertEqual(self.cat.get('test_story')['title'],'Test story')
        with self.assertRaises(KeyError):self.cat.get('missing')

if __name__=='__main__':unittest.main()
