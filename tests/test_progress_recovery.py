"""Position-only history recovery; both old and modern positions define story cutoffs."""
import json,shutil,sqlite3,tempfile,threading,unittest,urllib.request
from pathlib import Path
from backend.ai import Nova
from backend.catalog import Catalog
from backend.home import HomeService
from server import create_server
ROOT=Path(__file__).resolve().parents[1]

class ProgressRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.rt=self.root/'runtime';self.rt.mkdir()
        shutil.copytree(ROOT/'assets',self.root/'assets',ignore=shutil.ignore_patterns('*.mp4'))
        for path in ('assets/cartoon/spring/movie.mp4','assets/cartoon/sprite_fright/movie.mp4'):
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'controlled test media')
        with sqlite3.connect(self.rt/'viewers.sqlite3') as c:c.execute('CREATE TABLE watch_progress (sid TEXT,episode_id INTEGER,position REAL,duration REAL,completed INTEGER,updated REAL,PRIMARY KEY(sid,episode_id))')
    def tearDown(self):self.tmp.cleanup()
    def legacy(self,eid=4,position=240,sid='viewer'):
        with sqlite3.connect(self.rt/'viewers.sqlite3') as c:c.execute('INSERT INTO watch_progress VALUES (?,?,?,?,?,?)',(sid,eid,position,999,0,100))
    def episode(self,h,eid=4,sid='viewer'):
        return next(e for m in h.get(sid)['media'] for e in m['episodes'] if e['id']==eid)
    def test_cartoon_resume_at_four_minutes_immediately_unlocks_seen_recap(self):
        self.legacy();self.legacy(5)
        h=HomeService(self.root,self.rt)
        for eid,unit in ((4,'spring_e01'),(5,'sprite_fright_e01')):
            e=self.episode(h,eid);self.assertTrue(e['recap_ready']);self.assertEqual(e['watched_ranges'],[[0,240]]);self.assertEqual(e['progress']['position'],240)
            self.assertEqual(h.scope_manifest('viewer',Catalog(self.root).get(unit))['watched_ranges'],[[0,240]])
    def test_recovery_is_idempotent_across_restarts_and_scoped_to_viewer(self):
        self.legacy();h=HomeService(self.root,self.rt);h=HomeService(self.root,self.rt)
        self.assertEqual(self.episode(h)['watched_ranges'],[[0,240]]);self.assertEqual(self.episode(h,sid='other')['watched_ranges'],[])
        with sqlite3.connect(h.db) as c:self.assertEqual(c.execute("SELECT count(*) FROM watched_range WHERE sid='viewer' AND episode_id=4").fetchone()[0],1)
    def test_resume_position_defines_scope_despite_short_watched_intervals(self):
        self.legacy()
        with sqlite3.connect(self.rt/'viewers.sqlite3') as c:
            c.execute('CREATE TABLE watched_range (sid TEXT,episode_id INTEGER,start REAL,end REAL)');c.execute("INSERT INTO watched_range VALUES ('viewer',4,220,240)")
        h=HomeService(self.root,self.rt);self.assertEqual(self.episode(h)['watched_ranges'],[[0,240]])
    def test_modern_seek_only_save_unlocks_prefix_after_restart(self):
        h=HomeService(self.root,self.rt);h.save('viewer',dict(episode_id=4,position=240,watched_ranges=[]))
        h=HomeService(self.root,self.rt);e=self.episode(h)
        self.assertEqual(e['progress']['position'],240);self.assertEqual(e['watched_ranges'],[[0,240]]);self.assertTrue(e['recap_ready'])
    def test_modern_partial_watch_includes_skipped_gap_on_restart(self):
        h=HomeService(self.root,self.rt);h.save('viewer',dict(episode_id=4,position=240,watched_ranges=[[0,15],[220,240]]))
        h=HomeService(self.root,self.rt);self.assertEqual(self.episode(h)['watched_ranges'],[[0,240]])
    def test_legacy_recovery_clamps_to_actual_asset_duration(self):
        self.legacy(position=9999);h=HomeService(self.root,self.rt);e=self.episode(h)
        self.assertEqual(e['watched_ranges'],[[0,e['duration']]])
    def test_invalid_legacy_positions_do_not_create_watched_evidence(self):
        self.legacy(position=-1);self.legacy(5,position=None);h=HomeService(self.root,self.rt)
        self.assertEqual(self.episode(h,4)['watched_ranges'],[]);self.assertEqual(self.episode(h,5)['watched_ranges'],[])
    def test_http_previously_metadata_accepts_recovered_cartoon_without_waiting(self):
        sid='a'*32;self.legacy(sid=sid)
        server=create_server(root=self.root,runtime=self.rt,port=0,ai=Nova(self.rt,key=''));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            req=urllib.request.Request(base+'/api/movie/spring_e01',headers={'Cookie':'viewer='+sid})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.status,200);self.assertEqual(json.load(response)['movie']['id'],'spring_e01')
            req=urllib.request.Request(base+'/api/movie/spring_e01/transcript',headers={'Cookie':'viewer='+sid})
            with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['watched_ranges'],[[0,240]])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
