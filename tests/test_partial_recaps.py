"""Watched-only model context and edits; fixture AI responses are never production data."""
import copy,http.cookiejar,json,shutil,sqlite3,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from backend.catalog import Catalog,read_srt
from backend.home import HomeService
from backend.pipeline import Pipeline
from backend.service import Service,initial_state
from backend.watched_scope import windows,covers,scoped_cues,clamp_ranges,context_cues
from server import create_server
ROOT=Path(__file__).resolve().parents[1]

class FixtureAI:
    provider='nova';key='fixture-only';model='fixture';label='Controlled test AI'
    def __init__(self):self.calls=[]
    def log(self,*a):pass
    def invalidate(self,*a):pass
    def json(self,task,instruction,data,schema,progress,max_tokens):
        self.calls.append((task,copy.deepcopy(data)))
        if task=='extract':
            cues=data['cues'];a,b=cues[0]['id'],cues[-1]['id']
            return dict(events=[dict(title='Fixture dialogue',summary='Controlled fixture claim',category='secondary',why='Fixture only',characters=[],threads=['fixture'],location='Not stated',evidence=cues[0]['text'],ranges=[dict(first=a,last=b)],relationships=[])],coverage=[dict(first=a,last=b,category='secondary',why='Fixture only')])
        if task=='review':return dict(events=[dict(index=e['index'],supported=True,reason='Controlled fixture verdict',unsupported_relationships=[]) for e in data['events']],unsupported_coverage=[])
        if task=='scene_coverage':return dict(scenes=[dict(index=i['index'],category='secondary',title='Fixture visual scene',summary='Controlled fixture description',why='Fixture only',characters=[],visual_evidence='Controlled sampled test frame',evidence_cue_ids=[c['id'] for c in i['cues']]) for i in data['intervals']])
        if task=='recommend_scenes':
            c=next((c for c in data['candidates'] if c['frame_times']),None)
            return dict(explanation='Controlled fixture edit',items=[dict(event_id=c['id'],reason='Fixture edit of seen material',ranges=c['allowed_ranges'])] if c else [])
        raise AssertionError('Unexpected AI task '+task)

class PartialRecapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.rt=self.root/'runtime';self.rt.mkdir()
        shutil.copytree(ROOT/'assets',self.root/'assets',ignore=shutil.ignore_patterns('*.mp4'))
        (self.root/'frontend').mkdir();(self.root/'frontend/index.html').write_text('fixture')
        self.catalog=Catalog(self.root)
        for m in self.catalog.manifests():
            p=self.root/m['video'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'video-fixture')
        self.h=HomeService(self.root,self.rt,demo_start=True)
    def tearDown(self):self.tmp.cleanup()
    def save(self,eid=2,spans=None,position=None,sid='viewer'):
        spans=spans if spans is not None else [[0,45],[85,120]]
        return self.h.save(sid,dict(episode_id=eid,position=position if position is not None else spans[-1][1],watched_ranges=spans))
    def manifest(self,mid='tears_of_steel_e02',sid='viewer'):return self.h.scope_manifest(sid,self.catalog.get(mid))
    def test_partial_episode_and_movie_progress_survive_server_restart(self):
        self.save();self.save(eid=5,spans=[[10,80]],position=80)
        h=HomeService(self.root,self.rt,demo_start=True);d=h.get('viewer');byid={p['episode_id']:p for p in d['progress']}
        self.assertEqual(byid[2]['position'],120);self.assertEqual(byid[5]['position'],80);self.assertFalse(byid[2]['completed']);self.assertFalse(byid[5]['completed'])
        for unit in ('tears_of_steel_e02','sprite_fright_e01'):self.assertTrue(h.can_recap('viewer',unit))
        ep=d['media'][0]['episodes'][1];self.assertTrue(ep['recap_partial']);self.assertTrue(ep['recap_ready']);self.assertEqual(ep['watched_ranges'],[[0,45],[85,120]]);self.assertEqual(ep['watched_seconds'],80)
    def test_existing_partial_episode_one_is_not_overwritten_by_demo_seed(self):
        h=HomeService(self.root,self.rt);h.save('old',dict(episode_id=1,position=38,watched_ranges=[[0,38]]))
        h=HomeService(self.root,self.rt,True);p=h.get('old')['progress'][0]
        self.assertEqual(p['position'],38);self.assertFalse(p['completed']);self.assertEqual(h.scope_manifest('old',self.catalog.get('tears_of_steel_e01'))['watched_ranges'],[[0,38]])
    def test_seeking_changes_resume_position_but_does_not_expand_scope(self):
        self.save(spans=[[0,45]],position=45);before=self.manifest()
        self.save(spans=[],position=200);after=self.manifest();self.assertEqual(before['watched_ranges'],after['watched_ranges']);self.assertEqual(self.catalog.fingerprint(before),self.catalog.fingerprint(after))
    def test_tiny_seek_gap_is_not_merged_or_counted_as_seen(self):
        self.save(spans=[[0,30],[30.1,60]])
        self.assertEqual(self.manifest()['watched_ranges'],[[0,30],[30.1,60]])
        self.assertFalse(covers(windows(self.manifest()),29,31))
    def test_neighboring_chapter_rounding_does_not_unlock_an_unseen_fragment(self):
        self.save(spans=[[85,120]])
        self.assertEqual(self.manifest()['watched_ranges'],[[85,120]])
    def test_minimum_context_requires_one_contiguous_seen_window(self):
        self.save(spans=[[0,7],[85,92]]);self.assertFalse(self.h.can_recap('viewer','tears_of_steel_e02'))
        self.save(spans=[[7,13]]);self.assertTrue(self.h.can_recap('viewer','tears_of_steel_e02'))
        self.save(eid=4,spans=[[0,30]]);self.assertTrue(self.h.can_recap('viewer','spring_e01'))
    def test_scope_hash_separates_users_and_seen_versions(self):
        self.save(spans=[[0,45]]);self.save(spans=[[85,120]],sid='another')
        a,b=self.manifest(),self.manifest(sid='another');self.assertNotEqual(self.catalog.fingerprint(a),self.catalog.fingerprint(b))
        self.save(spans=[[45,60]]);self.assertNotEqual(self.catalog.fingerprint(a),self.catalog.fingerprint(self.manifest()))
    def test_boundary_subtitle_is_omitted_until_its_entire_text_was_seen(self):
        m=self.catalog.get('tears_of_steel_e02');path=self.catalog.inputs(m)[1];raw=read_srt(path);c=raw[0]
        bounded=dict(m,watched_ranges=[[0,(c['start']+c['end'])/2]])
        cues=scoped_cues(path,bounded);self.assertNotIn(c['source_cue_id'],{x['source_cue_id'] for x in cues})
        bounded['watched_ranges']=[[0,c['end']]];self.assertIn(c['source_cue_id'],{x['source_cue_id'] for x in scoped_cues(path,bounded)})
    def test_context_expansion_and_padding_cannot_bridge_a_gap(self):
        cues=[dict(id=i+1,start=i*10.,end=i*10+8.,text='Fixture') for i in range(8)]
        spans=[[0,30],[50,80]];event=dict(start=20,end=28,ranges=[dict(first=3,last=3,start=20,end=28)])
        local=context_cues(event,cues,spans);self.assertEqual([c['id'] for c in local],[1,2,3])
        self.assertEqual(clamp_ranges([dict(start=-.16,end=30.2)],spans),[dict(start=0,end=30)])
        self.assertFalse(covers(spans,20,60))
    def test_real_pipeline_only_sends_watched_cues_and_samples_seen_frames(self):
        self.save();m=self.manifest();ai=FixtureAI();p=Pipeline(self.catalog,ai,self.rt);sampled=[];rendered=[]
        def sample(video,folder,t):sampled.append(t);return 'data:image/jpeg;base64,dGVzdA=='
        def render(video,folder,ranges,progress):rendered.extend(ranges);return '/generated/fixture.mp4'
        with patch.object(p,'ffprobe',return_value=220),patch('backend.scenes.frame_image',side_effect=sample),patch.object(p,'render',side_effect=render):
            analysis,folder=p.analyze(m,lambda *a:None);queue=p.recommend(m,analysis,folder,initial_state(),lambda *a:None)
        self.assertTrue(queue['items']);self.assertTrue(sampled);self.assertTrue(rendered)
        self.assertEqual(analysis['watched_ranges'],[[0,45],[85,120]])
        self.assertTrue(all(any(a<=t<=b for a,b in m['watched_ranges']) for t in sampled))
        self.assertTrue(all(covers(windows(m),r['start'],r['end']) for r in rendered))
        def assert_cues(obj):
            if isinstance(obj,dict):
                if all(k in obj for k in ('start','end','text')):self.assertTrue(covers(windows(m),obj['start'],obj['end']),obj)
                for v in obj.values():assert_cues(v)
            elif isinstance(obj,list):
                for v in obj:assert_cues(v)
        for _,data in ai.calls:assert_cues(data)
        locked=[s for s in analysis['timeline'] if s['category']=='unwatched'];self.assertEqual([(x['start'],x['end']) for x in locked],[(45,85),(120,220)])
        self.assertTrue(all(covers(windows(m),e['start'],e['end']) for e in analysis['events']))
    def test_seconds_planner_rejects_cut_past_playhead_before_render(self):
        self.save(spans=[[0,30]]);m=self.manifest();ai=FixtureAI();p=Pipeline(self.catalog,ai,self.rt)
        event=dict(id='scene_1',title='Fixture',summary='Fixture',characters=[],category='secondary',ranges=[dict(start=0,end=30)],visual_scene=True,start=0,end=30,frame_times=[5,15,25],evidence='Fixture')
        def bad(*a,**k):return dict(explanation='Invalid fixture',items=[dict(event_id='scene_1',reason='Invalid fixture',ranges=[dict(start=0,end=31)])])
        with patch.object(ai,'json',side_effect=bad),patch.object(p,'render') as render:
            with self.assertRaisesRegex(ValueError,'watched portions'):p.recommend_seconds(m,dict(events=[event]),self.rt,initial_state(),[event],[],lambda *a:None)
            render.assert_not_called()
    def test_service_resets_stale_queue_when_more_watched_material_is_added(self):
        self.save(spans=[[0,45]]);s=Service(self.root,self.rt,FixtureAI());s.home=self.h;old=s.manifest('viewer','tears_of_steel_e02')
        state=initial_state();state['fingerprint']=s.catalog.fingerprint(old);state['queue']=dict(items=[dict(event_id='old')]);s.save('viewer',old['id'],state)
        self.save(spans=[[45,60]]);d=s.movie('viewer',old['id']);self.assertIsNone(d['queue']);self.assertEqual(self.h.get('viewer')['progress'][0]['position'],60)
    def test_near_end_resume_completion_does_not_reveal_unseen_final_facts(self):
        media=self.h.catalog.library()[0]
        self.save(spans=[[0,219]],position=219)
        self.save(eid=3,spans=[[0,media['episodes'][2]['duration']]])
        e=self.h.get('viewer')['media'][0]['episodes'][1]
        self.assertTrue(e['progress']['completed']);self.assertTrue(e['recap_partial']);self.assertFalse(e['watched'])
        self.assertFalse(self.h.story_map('viewer',1)['complete'])
        self.assertTrue(all(not c['description'] for c in self.h.story_map('viewer',1)['characters']))
    def test_partial_map_selection_uses_latest_seen_episode(self):
        self.save(spans=[[0,40]]);self.assertEqual(self.h.story_map('viewer',1)['recap_unit'],'tears_of_steel_e02')
    def test_partial_http_metadata_transcript_and_safe_source_preview(self):
        server=create_server(port=0,root=self.root,runtime=self.rt,ai=FixtureAI());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port);opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def get(path):
            with opener.open(base+path) as r:return json.load(r)
        def post(path,body):
            req=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with opener.open(req) as r:return json.load(r)
        try:
            get('/api/home');post('/api/progress',dict(episode_id=2,position=120,watched_ranges=[[0,45],[85,120]]))
            metadata=get('/api/movie/tears_of_steel_e02');self.assertIsNone(metadata['analysis']);self.assertEqual(metadata['movie']['synopsis'],'')
            script=get('/api/movie/tears_of_steel_e02/transcript');self.assertTrue(script['cues']);self.assertTrue(all(covers(script['watched_ranges'],c['start'],c['end']) for c in script['cues']))
            with self.assertRaises(urllib.error.HTTPError) as err:get('/inputs/tears_of_steel_e02/assets/tv_show/tears_of_steel/transcript/episode_02.json')
            self.assertEqual(err.exception.code,403)
            with patch.object(server.service.pipeline,'render',return_value='/generated/safe.mp4') as render:
                for a,b in ((40,90),(120,130),(-1,20),(0,200)):
                    with self.assertRaises(urllib.error.HTTPError) as err:post('/api/movie/tears_of_steel_e02/source-preview',dict(start=a,end=b))
                    self.assertEqual(err.exception.code,400)
                render.assert_not_called()
                self.assertEqual(post('/api/movie/tears_of_steel_e02/source-preview',dict(start=85,end=110))['clip'],'/generated/safe.mp4');render.assert_called_once()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
