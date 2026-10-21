"""Database discovery, watched intervals, and an explicit episode-two demo seed."""
import copy,math,sqlite3,time,threading
from .media_catalog import MediaCatalog
from .story_map import merge_ranges,contains,visible_story
from .watched_scope import MIN_RECAP_SECONDS

class HomeService:
    def __init__(self,root,runtime,demo_start=False):
        self.catalog=MediaCatalog(root);self.db=runtime/'viewers.sqlite3';self.demo_start=demo_start
        self.seeded=set();self.guard=threading.RLock()
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE IF NOT EXISTS watch_progress (sid TEXT,episode_id INTEGER,position REAL,duration REAL,completed INTEGER,updated REAL,PRIMARY KEY(sid,episode_id))')
            c.execute('CREATE TABLE IF NOT EXISTS watched_range (sid TEXT,episode_id INTEGER,start REAL,end REAL)')
            c.execute('CREATE TABLE IF NOT EXISTS watch_tracking (sid TEXT,episode_id INTEGER,version INTEGER,PRIMARY KEY(sid,episode_id))')
        self.recover_legacy_progress()
    def recover_legacy_progress(self):
        """One-time recovery of position-only progress from older app releases.
        Existing interval evidence always wins. Modern saves are marked even
        when empty (e.g. seek-only), so restarts never infer their skipped footage.
        """
        durations={e['id']:float(e['duration'] or 0) for m in self.catalog.library() for e in m['episodes']}
        with self.guard,sqlite3.connect(self.db) as c:
            rows=c.execute('SELECT p.sid,p.episode_id,p.position FROM watch_progress p LEFT JOIN watch_tracking t ON t.sid=p.sid AND t.episode_id=p.episode_id WHERE t.version IS NULL').fetchall()
            for sid,eid,position in rows:
                known=c.execute('SELECT 1 FROM watched_range WHERE sid=? AND episode_id=? LIMIT 1',(sid,eid)).fetchone()
                if not known and type(position) in (int,float) and math.isfinite(position) and position>0:
                    end=min(position,durations.get(eid,0))
                    if end>0:c.execute('INSERT INTO watched_range VALUES (?,?,?,?)',(sid,eid,0,end))
                c.execute('INSERT OR IGNORE INTO watch_tracking VALUES (?,?,1)',(sid,eid))

    def seed(self,sid):
        if not self.demo_start:return
        with self.guard:
            if sid in self.seeded:return
            m=next((m for m in self.catalog.library() if m['slug']=='tears_of_steel'),None)
            if m and len(m['episodes'])>1:
                e=m['episodes'][0];duration=float(e['duration'] or 0)
                with sqlite3.connect(self.db) as c:
                    ids=[x['id'] for x in m['episodes']]
                    if c.execute('SELECT 1 FROM watch_progress WHERE sid=? AND episode_id IN ('+','.join('?' for _ in ids)+') LIMIT 1',[sid]+ids).fetchone():
                        self.seeded.add(sid);return
                    c.execute('INSERT OR REPLACE INTO watch_progress VALUES (?,?,?,?,?,?)',(sid,e['id'],duration,duration,1,time.time()))
                    c.execute('INSERT INTO watched_range VALUES (?,?,?,?)',(sid,e['id'],0,duration))
                    c.execute('INSERT OR REPLACE INTO watch_tracking VALUES (?,?,1)',(sid,e['id']))
            self.seeded.add(sid)
    def watched(self,sid,media):
        self.seed(sid)
        with sqlite3.connect(self.db) as c:rows=c.execute('SELECT episode_id,start,end FROM watched_range WHERE sid=?',(sid,)).fetchall()
        eps={e['id']:e for e in media['episodes']}
        return merge_ranges([(eps[eid]['original_start']+a,min(eps[eid]['original_start']+b,eps[eid]['original_end'] or float('inf'))) for eid,a,b in rows if eid in eps])
    def story_map(self,sid,mid,at=None):
        media=next((m for m in self.catalog.library() if m['id']==mid),None)
        if not media:raise KeyError('Unknown title')
        watched=self.watched(sid,media)
        if at is not None:
            if type(at) not in (int,float) or not math.isfinite(at) or at<0:raise ValueError('Invalid story-map timestamp')
            watched=[[a,min(b,at)] for a,b in watched if a<at]
        result=visible_story(media,watched)
        if at is not None:result['as_of_seconds']=at
        eligible=[e for e in media['episodes'] if e['recap_ready'] and any(b-a>=MIN_RECAP_SECONDS for a,b in self.episode_ranges(sid,media,e))]
        result['recap_unit']=eligible[-1]['unit_id'] if eligible else None
        return result
    def public_analysis(self,sid,media_id,analysis):
        if analysis is None:return None
        story=self.story_map(sid,media_id);result=copy.deepcopy(analysis)
        ids={c['id'] for c in story['characters']}
        allowed={(r['source'],r['target'],r['label'],r['explanation']) for r in story['relationships']}
        result['characters']=[];result['relationships']=[]
        for event in result.get('events',[]):
            event['characters']=[cid for cid in event.get('characters',[]) if cid in ids]
            event['relationships']=[r for r in event.get('relationships',[]) if (r['source'],r['target'],r['label'],r['explanation']) in allowed]
        return result
    def episode_ranges(self,sid,media,episode):
        self.seed(sid)
        with sqlite3.connect(self.db) as c:
            rows=c.execute('SELECT start,end FROM watched_range WHERE sid=? AND episode_id=?',(sid,episode['id'])).fetchall()
        # Use this episode's own viewing records; container/audio rounding at a
        # neighboring chapter boundary must not unlock even a fragment here.
        duration=float(episode['duration'] or 0);result=[]
        for a,b in merge_ranges(rows):
            start=math.ceil(max(0,a)*1000-1e-8)/1000
            end=math.floor(min(duration,b)*1000+1e-8)/1000
            if end>start:result.append([start,end])
        return result
    def scope_manifest(self,sid,manifest):
        m=next((m for m in self.catalog.library() if m['id']==manifest.get('media_id')),None)
        if not m:return manifest
        e=next(e for e in m['episodes'] if e['id']==manifest['episode_id'])
        spans=self.episode_ranges(sid,m,e)
        if not any(b-a>=MIN_RECAP_SECONDS for a,b in spans):raise ValueError('Watch at least 12 continuous seconds before opening a recap.')
        # A public manifest field participates in the analysis/cache fingerprint.
        return dict(manifest,watched_ranges=spans)
    def can_recap(self,sid,unit):
        for m in self.catalog.library():
            for e in m['episodes']:
                if e['unit_id']==unit:return e['recap_ready'] and any(b-a>=MIN_RECAP_SECONDS for a,b in self.episode_ranges(sid,m,e))
        return False
    def transcript_visible(self,sid,unit):
        for m in self.catalog.library():
            for e in m['episodes']:
                if e['unit_id']==unit:return contains(self.watched(sid,m),e['original_start'],e['original_start']+float(e['duration'] or 0)-.01)
        return False
    def get(self,sid):
        self.seed(sid);media=self.catalog.library()
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row;rows=[dict(r) for r in c.execute('SELECT episode_id,position,duration,completed,updated FROM watch_progress WHERE sid=? ORDER BY updated DESC',(sid,))]
        lookup={r['episode_id']:r for r in rows}
        for m in media:
            watched=self.watched(sid,m);story=visible_story(m,watched);chars={c['database_id']:c for c in story['characters']}
            m['characters']=[dict(c,bio=chars[c['id']]['description'],presence={},actor=dict(c['actor'],bio=c['actor'].get('bio','') if story['complete'] else '') if c['actor'] else None) for c in m['characters'] if c['id'] in chars]
            m['relationships']=story['relationships'];m['about']=m['about'] if story['complete'] else ''
            for e in m['episodes']:
                e['progress']=lookup.get(e['id']);e['watched']=contains(watched,e['original_start'],e['original_start']+float(e['duration'] or 0)-.01)
                if not e['watched']:e['about']=''
                spans=self.episode_ranges(sid,m,e)
                e['watched_ranges']=spans;e['watched_seconds']=round(sum(b-a for a,b in spans),3)
                e['recap_partial']=not e['watched']
                e['recap_ready']=e['recap_ready'] and any(b-a>=MIN_RECAP_SECONDS for a,b in spans)
        return dict(media=media,progress=rows,demo_start=self.demo_start,profile=dict(name='Your profile',completed_episodes=sum(r['completed'] for r in rows)))
    def save(self,sid,data):
        self.seed(sid)
        if type(data.get('episode_id')) is not int:raise ValueError('Invalid episode')
        e=next((e for m in self.catalog.library() for e in m['episodes'] if e['id']==data['episode_id']),None)
        if not e or not e['ready']:raise ValueError('Playback asset is not available')
        p=data.get('position')
        if type(p) not in (int,float) or not math.isfinite(p) or p<0:raise ValueError('Invalid playback position')
        duration=float(e['duration']);p=min(p,duration);segments=data.get('watched_ranges',[])
        if not isinstance(segments,list) or len(segments)>100:raise ValueError('Invalid watched ranges')
        for span in segments:
            if not isinstance(span,list) or len(span)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in span) or not 0<=span[0]<span[1]<=duration+.2:raise ValueError('Invalid watched range')
        with self.guard,sqlite3.connect(self.db) as c:
            old=c.execute('SELECT start,end FROM watched_range WHERE sid=? AND episode_id=?',(sid,e['id'])).fetchall()
            ranges=merge_ranges(old+segments);complete=contains(ranges,0,max(0,duration-2))
            c.execute('DELETE FROM watched_range WHERE sid=? AND episode_id=?',(sid,e['id']))
            c.executemany('INSERT INTO watched_range VALUES (?,?,?,?)',[(sid,e['id'],a,min(b,duration)) for a,b in ranges])
            c.execute('INSERT OR REPLACE INTO watch_progress VALUES (?,?,?,?,?,?)',(sid,e['id'],p,duration,int(complete),time.time()))
            c.execute('INSERT OR REPLACE INTO watch_tracking VALUES (?,?,1)',(sid,e['id']))
        return dict(saved=True,position=p,completed=complete)
