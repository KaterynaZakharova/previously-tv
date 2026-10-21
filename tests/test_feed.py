"""Feed feedback persistence and original-clock map snapshots; no provider calls."""
import copy,json,shutil,sqlite3,tempfile,threading,unittest,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
from unittest.mock import patch
from backend.catalog import Catalog
from backend.home import HomeService
from backend.media_catalog import seconds
from backend.service import Service,initial_state
from server import create_server
ROOT=Path(__file__).resolve().parents[1]
class NoNetworkAI:
    key='fixture';model='fixture';provider='nova';label='Fixture'
    def json(self,*a,**k):raise AssertionError('No live AI in a feedback/map test')
    def log(self,*a):pass
class FeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.rt=self.root/'runtime';self.rt.mkdir()
        shutil.copytree(ROOT/'assets',self.root/'assets',ignore=shutil.ignore_patterns('*.mp4'))
        (self.root/'frontend').mkdir();(self.root/'frontend/index.html').write_text('fixture')
        self.h=HomeService(self.root,self.rt,True);self.s=Service(self.root,self.rt,NoNetworkAI());self.s.home=self.h
        for m in self.s.catalog.manifests():
            p=self.root/m['video'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture-video')
        self.mid='tears_of_steel_e01';self.m=self.s.manifest('viewer',self.mid);self.digest=self.s.catalog.fingerprint(self.m)
        self.analysis=dict(fingerprint=self.digest,events=[dict(id='a',start=30,end=50,characters=[],relationships=[]),dict(id='b',start=50,end=70,characters=[],relationships=[]),dict(id='unauthorized',start=80,end=100,characters=[],relationships=[])])
        folder=self.rt/'artifacts'/self.mid/self.digest;folder.mkdir(parents=True);(folder/'analysis.json').write_text(json.dumps(self.analysis))
        self.a=dict(event_id='a',duration=20,ranges=[dict(start=30,end=50)],clip='/a.mp4');self.b=dict(event_id='b',duration=20,ranges=[dict(start=50,end=70)],clip='/b.mp4')
        self.state=initial_state();self.state.update(fingerprint=self.digest,memory={'a':1},queue=dict(items=[self.b],total_seconds=20),clip_history=[self.a,self.b],revision=1)
        self.s.save('viewer',self.mid,self.state)
    def tearDown(self):self.tmp.cleanup()
    def test_up_can_rate_a_remembered_clip_that_left_the_queue(self):
        with patch('backend.service.threading.Thread'):
            job=self.s.start('viewer',self.mid,dict(type='not_remember',event_id='a'))
        self.assertEqual(job['state']['memory']['a'],0);self.assertEqual(job['state']['context_depth'],1);self.assertEqual([q['event_id'] for q in job['queue']['items']],['b'])
    def test_signal_is_immediate_and_does_not_create_an_ai_job(self):
        result=self.s.signal('viewer',self.mid,dict(type='not_remember',event_id='a'))
        self.assertTrue(result['saved']);self.assertEqual(result['state']['memory']['a'],0);self.assertFalse(self.s.jobs)
        self.assertEqual(self.s.state('viewer',self.mid)['context_depth'],1)
        result=self.s.signal('viewer',self.mid,dict(type='remember',event_id='a'));self.assertEqual(result['state']['memory']['a'],1)
    def test_signal_cannot_rate_another_viewer_or_a_never_queued_event(self):
        with self.assertRaises(ValueError):self.s.signal('viewer',self.mid,dict(type='remember',event_id='unauthorized'))
        with self.assertRaises(ValueError):self.s.signal('different',self.mid,dict(type='remember',event_id='a'))
    def test_expand_uses_persisted_forget_without_counting_the_gesture_twice(self):
        self.s.signal('viewer',self.mid,dict(type='not_remember',event_id='a'))
        with patch('backend.service.threading.Thread'):
            job=self.s.start('viewer',self.mid,dict(type='expand',event_id='a'))
        self.assertEqual(job['state']['context_depth'],1);self.assertEqual(job['action'],'not_remember')
    def test_cancelled_forget_is_not_expanded_after_a_later_remember(self):
        self.s.signal('viewer',self.mid,dict(type='remember',event_id='a'))
        with self.assertRaisesRegex(ValueError,'no longer'):self.s.start('viewer',self.mid,dict(type='expand',event_id='a'))
    def test_memory_saved_while_ai_runs_wins_over_old_job_state(self):
        state=copy.deepcopy(self.state);state.update(action='not_remember',memory={'a':0});self.s.save('viewer',self.mid,state)
        self.s.jobs['job']=dict(status='running',state=copy.deepcopy(state))
        def recommend(*a):
            self.s.signal('viewer',self.mid,dict(type='remember',event_id='a'))
            return dict(items=[self.a],explanation='Fixture context',model='fixture')
        with patch.object(self.s.pipeline,'analyze',return_value=(self.analysis,self.rt)),patch.object(self.s.pipeline,'expand_context',return_value=self.analysis),patch.object(self.s.pipeline,'recommend',side_effect=recommend):self.s.work('job',self.m,copy.deepcopy(state),'viewer')
        self.assertEqual(self.s.jobs['job']['status'],'ready');self.assertEqual(self.s.state('viewer',self.mid)['memory']['a'],1);self.assertNotIn('a',{q['event_id'] for q in self.s.jobs['job']['queue']['items']})
    def test_forget_saved_while_ai_runs_cannot_requeue_the_returned_clip(self):
        state=copy.deepcopy(self.state);state['action']='build';self.s.save('viewer',self.mid,state)
        self.s.jobs['job']=dict(status='running',state=copy.deepcopy(state))
        def recommend(*a):
            self.s.signal('viewer',self.mid,dict(type='not_remember',event_id='b'))
            return dict(items=[self.b],explanation='Fixture context',model='fixture')
        with patch.object(self.s.pipeline,'analyze',return_value=(self.analysis,self.rt)),patch.object(self.s.pipeline,'recommend',side_effect=recommend):self.s.work('job',self.m,copy.deepcopy(state),'viewer')
        self.assertEqual(self.s.state('viewer',self.mid)['memory']['b'],0)
        self.assertNotIn('b',{q['event_id'] for q in self.s.jobs['job']['queue']['items']})
    def test_map_hides_connections_until_the_clip_source_timestamp(self):
        media=self.h.catalog.library()[0]
        for ep in media['episodes']:self.h.save('viewer',dict(episode_id=ep['id'],position=ep['duration'],watched_ranges=[[0,ep['duration']]]))
        early=self.h.story_map('viewer',1,at=0);self.assertEqual(early['characters'],[]);self.assertEqual(early['relationships'],[])
        before=self.h.story_map('viewer',1,at=150)
        self.assertEqual(before['as_of_seconds'],150);self.assertTrue(all(r['end']<=150 for r in before['relationships']));self.assertTrue(all(not c['description'] for c in before['characters']))
        expected={r['id'] for r in media['relationships'] if seconds(r['timestamp_to'])<=150};self.assertEqual({r['id'] for r in before['relationships']},expected)
        self.assertLess(len(before['relationships']),len(self.h.story_map('viewer',1)['relationships']))
    def test_map_timestamp_does_not_unlock_unwatched_source(self):
        self.assertEqual(self.h.story_map('viewer',1,at=999)['relationships'],self.h.story_map('viewer',1)['relationships'])
        for value in (-1,float('inf'),float('nan'),True):
            with self.assertRaises(ValueError):self.h.story_map('viewer',1,at=value)
    def test_http_snapshot_and_feedback_routes(self):
        server=create_server(port=0,root=self.root,runtime=self.rt,ai=NoNetworkAI());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            req=urllib.request.Request(base+'/api/story-map/1?at=0',headers={'Cookie':'viewer='+'a'*32})
            with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['characters'],[])
            req=urllib.request.Request(base+'/api/story-map/1?at=nan',headers={'Cookie':'viewer='+'a'*32})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
            # A matching opaque viewer cookie owns this verified queue.
            sid='b'*32;state=copy.deepcopy(self.state);server.service.save(sid,self.mid,state)
            req=urllib.request.Request(base+'/api/movie/'+self.mid+'/signal',data=b'{"type":"not_remember","event_id":"a"}',headers={'Content-Type':'application/json','Cookie':'viewer='+sid})
            with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['saved'])
            self.assertFalse(server.service.jobs)
        finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
