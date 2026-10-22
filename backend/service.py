"""Per-viewer persistent queues. Remember is a local database operation, never AI."""
import copy
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from .ai import AIUnavailable, make_ai
from .catalog import Catalog
from .pipeline import Pipeline
from .logbook import Logbook

def initial_state():
    return dict(memory={},interest={},last_rated=None,character=None,pair=None,budget=180,revision=0,
                queue=None,clip_history=[],context_depth=0)

class Service:
    def __init__(self,root,runtime=None,ai=None):
        self.root=Path(root);self.runtime=Path(runtime or self.root/'runtime')
        self.runtime.mkdir(parents=True,exist_ok=True);self.db=self.runtime/'viewers.sqlite3'
        with sqlite3.connect(self.db) as c:c.execute('CREATE TABLE IF NOT EXISTS viewers (sid TEXT, movie TEXT, state TEXT, PRIMARY KEY(sid,movie))')
        self.catalog=Catalog(self.root);self.ai=ai or make_ai(self.runtime)
        self.pipeline=Pipeline(self.catalog,self.ai,self.runtime)
        self.jobs={};self.active={};self.guard=threading.RLock();self.analyses={}
    def manifest(self,sid,mid):
        m=self.catalog.get(mid)
        return self.home.scope_manifest(sid,m) if getattr(self,'home',None) and m.get('media_id') else m
    def state(self,sid,mid):
        with sqlite3.connect(self.db) as c:row=c.execute('SELECT state FROM viewers WHERE sid=? AND movie=?',(sid,mid)).fetchone()
        s=initial_state()
        if row:s.update(json.loads(row[0]))
        return s
    def save(self,sid,mid,state):
        with sqlite3.connect(self.db) as c:c.execute('INSERT OR REPLACE INTO viewers VALUES (?,?,?)',(sid,mid,json.dumps(state)))
    def analysis(self,m):
        try:
            digest=self.catalog.fingerprint(m);cache=self.runtime/'artifacts'/m['id']/digest/'analysis.json'
            stored=Logbook(self.catalog).load(m)
            if stored:return Logbook(self.catalog).project(m,stored,digest)
            if cache.is_file():return json.loads(cache.read_text(encoding='utf-8'))
        except (ValueError,KeyError):pass
        return None
    def movie(self,sid,mid):
        m=self.manifest(sid,mid);s=self.state(sid,mid)
        if not m.get('catalog_only') and s.get('fingerprint')!=self.catalog.fingerprint(m):s=initial_state()
        with self.guard:
            jid=self.active.get((sid,mid));job=copy.deepcopy(self.jobs[jid]) if jid else None
            if job and job.get('fingerprint')!=self.catalog.fingerprint(m):job=None
        result=dict(movie=next(c for c in self.catalog.cards() if c['id']==mid),state=s,
                    analysis=(job.get('analysis') if job and job.get('analysis') else self.analysis(m)),
                    job=job,queue=s.get('queue'),ai_configured=bool(self.ai.key),logbook_ready=bool(Logbook(self.catalog).load(m)))
        if getattr(self,'home',None) and m.get('media_id'):
            result['analysis']=self.home.public_analysis(sid,m['media_id'],result['analysis'])
            if not self.home.transcript_visible(sid,mid):result['movie']['synopsis']=''
        return result
    def signal(self,sid,mid,action):
        """Persist remote feedback immediately, including while AI expands a queue."""
        m=self.manifest(sid,mid);kind=action.get('type');eid=action.get('event_id')
        if kind not in ('remember','not_remember'):raise ValueError('Invalid memory signal')
        known=self.analysis(m);events={e['id'] for e in known['events']} if known else set()
        with self.guard:
            state=self.state(sid,mid)
            if state.get('fingerprint')!=self.catalog.fingerprint(m):raise ValueError('Reopen the recap for your current playback position')
            allowed={q['event_id'] for q in (state.get('queue') or {}).get('items',[])}|{q['event_id'] for q in state.get('clip_history',[])}
            if eid not in events or eid not in allowed:raise ValueError('Rate a verified clip from your own recap history')
            state['memory'][eid]=1 if kind=='remember' else 0;state['last_rated']=eid
            if kind=='not_remember':state['context_depth']+=1
            if state.get('queue'):
                state['queue']['context_event_ids']=[]
                state['queue']['items']=[q for q in state['queue']['items'] if q['event_id']!=eid]
                state['queue']['total_seconds']=round(sum(q['duration'] for q in state['queue']['items']),2)
            state['revision']+=1;self.save(sid,mid,state)
            return dict(saved=True,state=copy.deepcopy(state),queue=copy.deepcopy(state.get('queue')))
    def start(self,sid,mid,action):
        m=self.manifest(sid,mid);self.catalog.inputs(m)
        with self.guard:
            old=self.active.get((sid,mid))
            if old and self.jobs[old]['status']=='running':raise ValueError('AI is still working. Wait for the current queue update.')
            s=self.state(sid,mid);fingerprint=self.catalog.fingerprint(m)
            if s.get('fingerprint') and s['fingerprint']!=fingerprint:s=initial_state()
            s['fingerprint']=fingerprint
            known=self.analysis(m)
            if getattr(self,'home',None) and m.get('media_id'):known=self.home.public_analysis(sid,m['media_id'],known)
            events={e['id']:e for e in known['events']} if known else {}
            kind=action.get('type','build');s['action']=kind
            if kind in ('remember','not_remember'):
                eid=action.get('event_id')
                if eid not in events:raise ValueError('Unknown event')
                queued={q['event_id'] for q in (s.get('queue') or {}).get('items',[])}
                history={q['event_id'] for q in s.get('clip_history',[])}
                if eid not in queued|history or (kind=='remember' and eid not in queued and s['memory'].get(eid)==1):raise ValueError('Rate a clip in your current queue or revisit a moment first')
                s['memory'][eid]=1 if kind=='remember' else 0;s['last_rated']=eid
                if s.get('queue'):s['queue']['items']=[q for q in s['queue']['items'] if q['event_id']!=eid]
                if kind=='not_remember':s['context_depth']+=1
            elif kind=='character':
                cid=action.get('character');ids={c['id'] for c in m['characters']}
                if cid is not None and cid not in ids:raise ValueError('Unknown character')
                s['character']=cid;s['pair']=None
                if cid:s['interest'][cid]=s['interest'].get(cid,0)+1
            elif kind=='pair':
                pair=action.get('pair');ids={c['id'] for c in m['characters']}
                if not isinstance(pair,list) or len(pair)!=2 or len(set(pair))!=2 or not set(pair)<=ids:raise ValueError('Invalid relationship')
                s['pair']=pair;s['character']=None
            elif kind=='expand':
                eid=action.get('event_id')
                if eid not in events or s['memory'].get(eid)!=0:raise ValueError('This moment is no longer marked as not remembered')
                s['last_rated']=eid;kind='not_remember';s['action']=kind
            elif kind=='reset':s=initial_state();s['fingerprint']=fingerprint;s['action']='reset'
            elif kind!='build':raise ValueError('Unknown action')
            if 'budget' in action:
                if action['budget'] not in (120,180,300,600):raise ValueError('Choose a supported recap length')
                s['budget']=action['budget']
            s['revision']+=1
            local=kind=='remember' or (kind=='build' and s.get('queue') is not None and not action.get('refresh')) or (kind=='character' and s['character'] is None)
            if s.get('queue'):
                s['queue']['total_seconds']=round(sum(q['duration'] for q in s['queue']['items']),2)
            self.save(sid,mid,s);jid=uuid.uuid4().hex
            job=dict(id=jid,movie_id=mid,fingerprint=fingerprint,status='ready' if local else 'running',stage='ready' if local else 'starting',
                message='Continuing your existing queue.' if local else ('Assembling your saved AI action moments…' if Logbook(self.catalog).load(m) else 'AI is thinking · finding new context to add to your queue…'),
                state=copy.deepcopy(s),analysis=known,queue=copy.deepcopy(s.get('queue')),error=None,action=kind,added_event_ids=[])
            self.jobs[jid]=job;self.active[(sid,mid)]=jid
            if not local:threading.Thread(target=self.work,args=(jid,m,copy.deepcopy(s),sid),daemon=True).start()
            return copy.deepcopy(job)
    def work(self,jid,m,state,sid):
        def progress(stage,message):
            with self.guard:self.jobs[jid].update(stage=stage,message=message)
        try:
            if not self.ai.key and not Logbook(self.catalog).load(m):raise AIUnavailable('AI unavailable — try later. Configure '+getattr(self.ai,'key_env','NOVA_API_KEY')+' on the Python server.')
            analysis,folder=self.pipeline.analyze(m,progress)
            if state.get('action') in ('character','pair','not_remember') or (state.get('action')=='build' and state.get('context_depth',0)>0):
                analysis=self.pipeline.expand_context(m,analysis,folder,state,progress)
            if getattr(self,'home',None) and m.get('media_id'):analysis=self.home.public_analysis(sid,m['media_id'],analysis)
            with self.guard:self.jobs[jid]['analysis']=analysis
            prior=copy.deepcopy(state.get('queue'))
            pending=prior['items'] if prior else []
            pending_ids={q['event_id'] for q in pending}
            recommendation=copy.deepcopy(state)
            # Prepared-but-unplayed clips may already contain the right prequel.
            # Permit promoting those clips instead of treating them as viewed.
            allowed_pending=pending_ids if state['action']=='not_remember' else set()
            recommendation['exclude_event_ids']=[q['event_id'] for q in state.get('clip_history',[]) if q['event_id'] not in allowed_pending]
            recommendation['clip_history']=[q for q in state.get('clip_history',[]) if q['event_id'] not in allowed_pending]
            fresh=self.pipeline.recommend(m,analysis,folder,recommendation,progress)
            new=[];ids=set(pending_ids)
            for q in fresh['items']:
                if q['event_id'] not in ids:new.append(q);ids.add(q['event_id'])
            context_ids=[q['event_id'] for q in fresh['items']] if state['action']=='not_remember' else []
            items=(fresh['items']+[q for q in pending if q['event_id'] not in set(context_ids)]) if context_ids else (pending+new)
            queue=dict(fresh,items=items,context_event_ids=context_ids,total_seconds=round(sum(q['duration'] for q in items),2))
            if not new and not context_ids and prior:queue['explanation']='No additional verified scene is available for this selection. Your remaining queue is kept. '+fresh['explanation']
            state.pop('exclude_event_ids',None);state['queue']=queue
            history={q['event_id']:q for q in state.get('clip_history',[])}
            history.update({q['event_id']:q for q in new});state['clip_history']=list(history.values())
            with self.guard:
                latest=self.state(sid,m['id'])
                if latest.get('fingerprint')==state.get('fingerprint') and latest['revision']>state['revision']:
                    for key in ('memory','last_rated','context_depth','revision'):state[key]=latest[key]
                    queue['items']=[q for q in queue['items'] if state['memory'].get(q['event_id'])!=1 and q['event_id']!=state['last_rated']]
                    queue['total_seconds']=round(sum(q['duration'] for q in queue['items']),2)
                self.save(sid,m['id'],state)
                self.jobs[jid].update(status='ready',stage='ready',message='Your queue is ready.',queue=queue,state=copy.deepcopy(state),added_event_ids=[q['event_id'] for q in new if q['event_id'] in {item['event_id'] for item in queue['items']}])
        except Exception as e:
            if isinstance(e,ValueError) and hasattr(self.ai,'invalidate'):self.ai.invalidate()
            self.ai.log(dict(task='job',status='failed',job_id=jid,movie=m['id'],error=str(e)))
            kind='provider' if isinstance(e,AIUnavailable) else 'analysis'
            with self.guard:self.jobs[jid].update(status='unavailable',stage='unavailable',message=str(e),error=str(e),error_type=kind,queue=state.get('queue'))
    def job(self,sid,mid,jid):
        with self.guard:
            if self.active.get((sid,mid))!=jid or jid not in self.jobs:raise KeyError('Unknown job for this viewer')
            job=copy.deepcopy(self.jobs[jid])
            if getattr(self,'home',None) and job.get('fingerprint')!=self.catalog.fingerprint(self.manifest(sid,mid)):raise ValueError('Your playback position changed. Reopen the recap for the new story cutoff.')
            return job
