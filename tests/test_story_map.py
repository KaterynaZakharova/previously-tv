"""DB-only maps, source-clock spoiler gates, and the repeatable episode-two demo."""
import copy,json,shutil,sqlite3,tempfile,threading,unittest,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
from unittest.mock import patch
from backend.ai import Nova
from backend import schemas
from backend.home import HomeService
from backend.media_catalog import MediaCatalog,seconds
from backend.story_map import tag_events
from backend.catalog import Catalog
from backend.pipeline import Pipeline
from server import create_server
ROOT=Path(__file__).resolve().parents[1]

class NoAI:
    key='';model='disabled';provider='nova';label='disabled'
    def json(self,*a,**k):raise AssertionError('A map must not call AI')
    def log(self,*a,**k):pass

class StoryMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.rt=self.root/'runtime';self.rt.mkdir()
        shutil.copytree(ROOT/'assets',self.root/'assets',ignore=shutil.ignore_patterns('*.mp4'))
        (self.root/'frontend').mkdir();(self.root/'frontend/index.html').write_text('fixture')
        self.cat=MediaCatalog(self.root)
        with self.cat.connection() as c:
            for r in c.execute('SELECT local_path FROM playback_asset'):
                p=self.root/r['local_path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'video-fixture')
        self.media=self.cat.library()[0]
    def tearDown(self):self.tmp.cleanup()
    def watch(self,h,e,a=0,b=None):
        b=e['duration'] if b is None else b
        return h.save('viewer',dict(episode_id=e['id'],position=b,watched_ranges=[[a,b]]))
    def test_no_history_exposes_no_characters_relationships_bios_or_synopses(self):
        h=HomeService(self.root,self.rt);m=h.get('viewer')['media'][0]
        self.assertEqual(m['characters'],[]);self.assertEqual(m['relationships'],[]);self.assertEqual(m['about'],'')
        self.assertTrue(all(e['about']=='' and not e['recap_ready'] for e in m['episodes']))
        self.assertEqual(h.story_map('viewer',1)['characters'],[])
    def test_fresh_demo_seeds_episode_one_and_preserves_progress_after_restart(self):
        h=HomeService(self.root,self.rt,True);first=h.get('viewer');self.assertTrue(first['progress'][0]['completed']);self.assertEqual(first['progress'][0]['episode_id'],1)
        self.watch(h,self.media['episodes'][1]);self.assertTrue(h.can_recap('viewer','tears_of_steel_e02'))
        h=HomeService(self.root,self.rt,True);d=h.get('viewer');self.assertEqual([p['episode_id'] for p in d['progress']],[2,1]);self.assertTrue(h.can_recap('viewer','tears_of_steel_e02'))
        self.assertTrue(h.get('new')['demo_start']);self.assertEqual(h.get('new')['progress'][0]['episode_id'],1)
    def test_seek_does_not_unlock_and_rewind_does_not_hide_watched_facts(self):
        h=HomeService(self.root,self.rt,True);before=h.story_map('viewer',1)
        e=self.media['episodes'][1];h.save('viewer',dict(episode_id=2,position=219))
        self.assertEqual(before,h.story_map('viewer',1));self.assertFalse(h.can_recap('viewer',e['unit_id']))
        self.watch(h,e,b=40);after=h.story_map('viewer',1);self.assertGreater(len(after['relationships']),len(before['relationships']))
        h.save('viewer',dict(episode_id=2,position=0));self.assertEqual(after,h.story_map('viewer',1))
    def test_cross_episode_relationship_uses_global_clock_not_episode_id(self):
        h=HomeService(self.root,self.rt,True);r=next(r for r in self.media['relationships'] if seconds(r['timestamp_from'])<221<seconds(r['timestamp_to'])<260)
        self.assertNotIn(r['id'],{x['id'] for x in h.story_map('viewer',1)['relationships']})
        self.watch(h,self.media['episodes'][1],b=40)
        shown=next(x for x in h.story_map('viewer',1)['relationships'] if x['id']==r['id'])
        self.assertEqual(shown['explanation'],r['cause']);self.assertEqual(shown['label'],r['type']);self.assertEqual(shown['basis'],'database')
    def test_all_db_characters_and_edges_after_completion_no_invented_connections(self):
        h=HomeService(self.root,self.rt)
        for e in self.media['episodes']:self.watch(h,e)
        result=h.story_map('viewer',1);self.assertTrue(result['complete'])
        self.assertEqual({c['database_id'] for c in result['characters']},{c['id'] for c in self.media['characters']})
        self.assertEqual({r['id'] for r in result['relationships']},{r['id'] for r in self.media['relationships']})
        self.assertEqual(len(result['characters']),11)
        for c in result['characters']:self.assertEqual(c['description'],next(x['bio'] for x in self.media['characters'] if x['id']==c['database_id']))
    def test_biography_and_future_presence_are_not_leaked(self):
        h=HomeService(self.root,self.rt,True);m=h.get('viewer')['media'][0];graph=h.story_map('viewer',1)
        self.assertTrue(all(c['bio']=='' for c in m['characters']));self.assertTrue(all(not c['description'] for c in graph['characters']))
        self.assertNotIn('c23',{c['id'] for c in graph['characters']})
        raw=json.dumps(m);self.assertNotIn('00:08:48.000',raw)
    def test_viewers_are_isolated_and_watched_ranges_survive_without_demo_reset(self):
        h=HomeService(self.root,self.rt);self.watch(h,self.media['episodes'][0]);self.assertTrue(h.can_recap('viewer','tears_of_steel_e01'))
        self.assertFalse(h.can_recap('someone_else','tears_of_steel_e01'))
        self.assertTrue(HomeService(self.root,self.rt).can_recap('viewer','tears_of_steel_e01'))
    def test_skipped_middle_never_marks_episode_complete(self):
        h=HomeService(self.root,self.rt);e=self.media['episodes'][0];self.watch(h,e,b=90)
        self.assertFalse(self.watch(h,e,a=120)['completed']);self.assertTrue(h.can_recap('viewer',e['unit_id']))
        self.watch(h,e,a=90,b=120);self.assertTrue(h.can_recap('viewer',e['unit_id']))
    def test_invalid_watched_ranges_rejected(self):
        h=HomeService(self.root,self.rt)
        for spans in ([[True,3]],[[-1,1]],[[10,1]],[[0,float('nan')]],[[0,999]],'bad'):
            with self.assertRaises(ValueError):h.save('viewer',dict(episode_id=1,position=0,watched_ranges=spans))
    def test_retrieval_tags_use_db_intervals_instead_of_ai_characters(self):
        m=next(m for m in self.cat.manifests() if m['id']=='tears_of_steel_e02')
        events=[dict(start=0,end=30,characters=['made_up'],relationships=[dict(source='fake',target='fake')])]
        tag_events(m,events);self.assertNotIn('made_up',events[0]['characters']);self.assertIn('c1',events[0]['characters'])
        self.assertTrue(events[0]['relationships']);self.assertTrue(all(r['basis']=='database' for r in events[0]['relationships']))
    def test_db_pipeline_skips_profile_and_cast_recovery_calls(self):
        cat=Catalog(self.root);m=cat.get('tears_of_steel_e01');pipeline=Pipeline(cat,NoAI(),self.rt)
        event=dict(id='e',start=100,end=130,characters=[],relationships=[],summary='Source fact')
        with patch.object(pipeline,'ffprobe',return_value=221),patch.object(pipeline,'extract_reviewed',return_value=([event],[])),patch('backend.pipeline.analyze_scenes',return_value=([dict(start=0,end=221,basis='sampled_visual')],[],[])),patch('backend.pipeline.recover_cast',side_effect=AssertionError('Do not infer a DB cast')):
            result,_=pipeline.analyze(m,lambda *a:None)
        self.assertEqual(result['characters'],[]);self.assertEqual(result['relationships'],[]);self.assertIn('c2',result['events'][0]['characters'])
    def test_empty_identity_schema_is_valid_and_locally_enforced(self):
        schema=schemas.scenes_for(set(),[0],set())
        chars=schema['properties']['scenes']['items']['properties']['characters']
        self.assertNotIn('enum',chars['items']);Nova.validate_schema([],chars)
        with self.assertRaises(ValueError):Nova.validate_schema(['invented'],chars)
    def test_public_analysis_omits_unwatched_db_relationship_causes(self):
        h=HomeService(self.root,self.rt,True)
        r=next(r for r in self.media['relationships'] if seconds(r['timestamp_from'])<221<seconds(r['timestamp_to'])<260)
        rel=dict(source='c'+str(r['character_from']),target='c'+str(r['character_to']),label=r['type'],explanation=r['cause'])
        analysis=dict(characters=[{'id':'invented'}],relationships=[rel],events=[dict(characters=['c1','c23'],relationships=[rel])])
        out=h.public_analysis('viewer',1,analysis);self.assertEqual(out['relationships'],[]);self.assertEqual(out['characters'],[]);self.assertEqual(out['events'][0]['characters'],['c1']);self.assertEqual(out['events'][0]['relationships'],[])
        self.assertEqual(len(analysis['relationships']),1)
        self.watch(h,self.media['episodes'][1],b=40)
        self.assertEqual(len(h.public_analysis('viewer',1,analysis)['events'][0]['relationships']),1)
    def test_http_map_has_no_ai_dependency_and_future_recap_is_blocked(self):
        server=create_server(port=0,root=self.root,runtime=self.rt,ai=NoAI());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port);opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            with patch.object(server.service.pipeline,'analyze',side_effect=AssertionError('Map called AI')):
                with opener.open(base+'/api/story-map/1') as r:d=json.load(r)
                self.assertEqual(d['source'],'database');self.assertEqual(len(d['characters']),10);self.assertEqual(d['recap_unit'],'tears_of_steel_e01');self.assertFalse(server.service.jobs)
            for path in ('/api/movie/tears_of_steel_e02','/inputs/tears_of_steel_e02/assets/tv_show/tears_of_steel/transcript/episode_02.json'):
                with self.assertRaises(urllib.error.HTTPError) as err:opener.open(base+path)
                self.assertEqual(err.exception.code,403)
            req=urllib.request.Request(base+'/api/movie/tears_of_steel_e03/action',data=b'{"type":"build"}',headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as err:opener.open(req)
            self.assertEqual(err.exception.code,403);self.assertFalse(server.service.jobs)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
