"""Database import, episode clocks, local asset protection and real progress."""
import copy,http.cookiejar,json,math,shutil,sqlite3,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from backend.media_catalog import MediaCatalog,normalize_database,seconds
from backend.catalog import Catalog,read_srt
from backend.home import HomeService
from backend.ai import Nova
from server import create_server
PROJECT=Path(__file__).resolve().parents[1]

class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        shutil.copytree(PROJECT/'assets',self.root/'assets',ignore=shutil.ignore_patterns('*.mp4','preparation_report.json'))
        (self.root/'frontend').mkdir();(self.root/'frontend/index.html').write_text('Home test')
        self.rt=self.root/'runtime';self.rt.mkdir();normalize_database(self.root);self.cat=MediaCatalog(self.root)
    def tearDown(self):self.tmp.cleanup()
    def playback(self,eid=1):
        with sqlite3.connect(self.cat.db) as c:
            row=c.execute('SELECT local_path FROM playback_asset WHERE episode_id=?',(eid,)).fetchone()
            path=row[0] if row else ('assets/tv_show/tears_of_steel/episodes/episode_%02d.mp4'%eid)
            c.execute('INSERT OR REPLACE INTO playback_asset VALUES (?,?,?,?,?,?,?)',(eid,path,221.,0,221,'https://example.invalid/source','fixture'))
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture-media-bytes');return p
    def test_three_parent_titles_and_five_ordered_episode_units(self):
        rows=self.cat.library();self.assertEqual([m['title'] for m in rows],['Tears of Steel','Spring','Sprite Fright']);self.assertEqual([len(m['episodes']) for m in rows],[3,1,1]);self.assertEqual(rows[0]['type'],'series');self.assertEqual(rows[0]['source_type'],'movie')
    def test_supplied_preview_and_character_paths_resolve(self):
        rows=self.cat.library();self.assertTrue(all(m['preview'] for m in rows));self.assertTrue(all(e['preview'] for m in rows for e in m['episodes']))
        self.assertEqual(len(rows[0]['characters']),11);self.assertTrue(all(c['photo'] for c in rows[0]['characters']));self.assertTrue(all(c['photo'] for m in rows[1:] for c in m['characters']))
        self.assertEqual(next(c for c in rows[1]['characters'] if c['id']==11)['name'],'Spring')
    def test_normalization_is_idempotent_and_preserves_original_urls(self):
        before=[m['source_url'] for m in self.cat.library()];normalize_database(self.root);normalize_database(self.root)
        self.assertEqual(before,[m['source_url'] for m in self.cat.library()]);self.assertEqual(len(self.cat.library()[0]['characters']),11)
    def test_future_cartoon_and_actor_images_are_picked_up_without_fake_photos(self):
        p=self.root/'assets/cartoon/spring/characters/01_spring.jpg';p.write_bytes(b'provided later')
        actor=self.root/'assets/images/cast/unknown_actor.jpg';actor.parent.mkdir(parents=True,exist_ok=True);actor.write_bytes(b'provided later')
        rows=self.cat.library();self.assertTrue(rows[1]['characters'][0]['photo'].endswith('01_spring.jpg'));self.assertTrue(rows[0]['characters'][0]['actor']['photo'].endswith('unknown_actor.jpg'))
        spring=next(m for m in Catalog(self.root).manifests() if m['id']=='spring_e01');self.assertEqual(spring['characters'][0]['photo'],'assets/cartoon/spring/characters/01_spring.jpg')
    def test_json_episode_clock_uses_local_timestamps_and_stable_source_ids(self):
        for order,offset in [(1,0),(2,221),(3,441)]:
            p=self.root/('assets/tv_show/tears_of_steel/transcript/episode_%02d.json'%order);raw=json.loads(p.read_text());cues=read_srt(p)
            self.assertEqual([c['id'] for c in cues],list(range(1,len(cues)+1)));self.assertEqual(cues[0]['source_cue_id'],raw['lines'][0]['id'])
            self.assertAlmostEqual(cues[0]['start'],seconds(raw['lines'][0]['start'])-offset);self.assertLessEqual(cues[-1]['end'],seconds(raw['original_end'])-offset)
            self.assertIn('not',cues[0]['speaker_attribution_note'].lower())
    def test_spring_plays_without_inventing_a_transcript(self):
        self.playback(4);m=self.cat.library()[1];self.assertTrue(m['episodes'][0]['ready']);self.assertTrue(m['episodes'][0]['recap_ready']);self.assertIsNone(m['episodes'][0]['transcript'])
    def test_source_assets_exclude_database_transcripts_and_traversal(self):
        for p in ('assets/db.sqlite','assets/tv_show/tears_of_steel/transcript/episode_01.json','assets/../server.py'):
            with self.assertRaises(KeyError):self.cat.public_asset(p)
        self.assertIsNotNone(self.cat.public_asset('assets/cartoon/spring/preview/media_01.jpg'))
    def test_progress_survives_service_restart_and_is_viewer_specific(self):
        self.playback();h=HomeService(self.root,self.rt);self.assertEqual(h.get('one')['progress'],[]);h.save('one',dict(episode_id=1,position=42));h2=HomeService(self.root,self.rt)
        self.assertEqual(h2.get('one')['progress'][0]['position'],42);self.assertEqual(h2.get('two')['progress'],[]);self.assertEqual(h2.get('one')['profile']['completed_episodes'],0)
    def test_completion_uses_real_asset_duration(self):
        self.playback();h=HomeService(self.root,self.rt)
        self.assertFalse(h.save('one',dict(episode_id=1,position=100,duration=10))['completed']);self.assertTrue(h.save('one',dict(episode_id=1,position=221,watched_ranges=[[0,221]]))['completed']);self.assertEqual(h.get('one')['profile']['completed_episodes'],1)
    def test_invalid_progress_is_rejected(self):
        self.playback();h=HomeService(self.root,self.rt)
        for d in (dict(episode_id=True,position=0),dict(episode_id=1,position=True),dict(episode_id=1,position=-1),dict(episode_id=1,position=float('nan')),dict(episode_id=999,position=0)):
            with self.assertRaises(ValueError):h.save('one',d)
    def test_http_home_progress_assets_and_range_with_real_session_cookie(self):
        self.playback();server=create_server(port=0,root=self.root,runtime=self.rt,ai=Nova(self.rt,key=''));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port);opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            with opener.open(base+'/api/home') as r:d=json.load(r)
            self.assertEqual(len(d['media']),3);self.assertEqual(d['progress'][0]['episode_id'],1);self.assertTrue(d['progress'][0]['completed'])
            req=urllib.request.Request(base+'/api/progress',data=json.dumps(dict(episode_id=1,position=32)).encode(),headers={'Content-Type':'application/json'})
            with opener.open(req) as r:self.assertTrue(json.load(r)['saved'])
            with opener.open(base+'/api/home') as r:self.assertEqual(json.load(r)['progress'][0]['position'],32)
            video=d['media'][0]['episodes'][0]['video'];req=urllib.request.Request(base+video,headers={'Range':'bytes=0-6'})
            with opener.open(req) as r:self.assertEqual(r.status,206);self.assertEqual(r.read(),b'fixture')
            with self.assertRaises(urllib.error.HTTPError) as e:opener.open(base+'/assets/db.sqlite')
            self.assertEqual(e.exception.code,404)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
