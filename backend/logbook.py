"""Persistent, source-hashed visual action annotations. No model calls during reuse.
Full-source preprocessing is explicit; every runtime projection is watched-only.
"""
import copy
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .ai import AIUnavailable
from .catalog import read_srt
from .scenes import frame_image
from .story_map import tag_events
from .watched_scope import covers, windows, scope_timeline

VERSION=1
SOURCE_CACHE={}
CATEGORIES=('critical','secondary','filler','unclassified')

def source_key(catalog,m):
    video,script=catalog.inputs(m)
    paths=[video]+([script] if script else [])
    signature=tuple((str(p.resolve()),p.stat().st_size,p.stat().st_mtime_ns) for p in paths)
    if signature not in SOURCE_CACHE:
        h=hashlib.sha256(('previously-action-logbook-%d'%VERSION).encode())
        for p in paths:
            h.update(b'\0'+str(p.stat().st_size).encode()+b'\0')
            with p.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        SOURCE_CACHE[signature]=h.hexdigest()
    return SOURCE_CACHE[signature]

class Logbook:
    def __init__(self,catalog):
        self.catalog=catalog;self.db=catalog.root/'assets/db.sqlite'
    def schema(self):
        if not self.db.is_file():raise ValueError('Import source metadata into assets/db.sqlite before preparing its persistent action logbook.')
        with sqlite3.connect(self.db) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS action_logbook_run (
              unit_id TEXT NOT NULL, source_key TEXT NOT NULL, version INTEGER NOT NULL,
              provider TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL,
              duration REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(unit_id,source_key));
            CREATE TABLE IF NOT EXISTS action_logbook_batch (
              unit_id TEXT NOT NULL, source_key TEXT NOT NULL, batch_index INTEGER NOT NULL,
              payload TEXT NOT NULL, PRIMARY KEY(unit_id,source_key,batch_index));
            CREATE TABLE IF NOT EXISTS action_logbook (
              unit_id TEXT NOT NULL, source_key TEXT NOT NULL, action_id TEXT NOT NULL,
              local_start REAL NOT NULL, local_end REAL NOT NULL,
              original_start REAL NOT NULL, original_end REAL NOT NULL,
              category TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
              evidence_json TEXT NOT NULL, PRIMARY KEY(unit_id,source_key,action_id));
            """)
    def load(self,m):
        if not self.db.is_file():return None
        key=source_key(self.catalog,m)
        with sqlite3.connect('file:'+self.db.resolve().as_posix()+'?mode=ro',uri=True) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='action_logbook_run'").fetchone():return None
            row=c.execute('SELECT payload FROM action_logbook_run WHERE unit_id=? AND source_key=? AND version=?',(m['id'],key,VERSION)).fetchone()
        return json.loads(row[0]) if row else None
    def project(self,m,stored,digest):
        result=copy.deepcopy(stored);result.pop('omitted_after_caption_review',None);spans=windows(m,stored['duration'])
        # An event's complete evidence envelope must be seen, including omitted filler.
        result['events']=[e for e in result['events'] if covers(spans,e['start'],e['end'])]
        tag_events(m,result['events']) if m.get('media_id') else None
        timeline=[]
        for t in result['timeline']:
            if covers(spans,t['start'],t['end']):timeline.append(t)
            else:timeline.append(dict(start=t['start'],end=t['end'],category='unclassified',basis='unassessed',title='',why='This annotation is withheld until its complete evidence interval has been watched.'))
        result['timeline']=scope_timeline(timeline,spans,stored['duration'])
        result.update(fingerprint=digest,watched_ranges=spans,watched_seconds=round(sum(b-a for a,b in spans),3),
            source_offset_seconds=float(m.get('original_start_seconds',0)),source='/inputs/'+m['id']+'/'+m['video'],
            characters=[],relationships=[],persistent_logbook=True)
        result['coverage_seconds']={k:round(sum(t['end']-t['start'] for t in result['timeline'] if t['basis']==k),3) for k in ('sampled_visual','unassessed','unwatched')}
        return result


def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def arr(items):return dict(type='array',items=items)
STR=dict(type='string');NUM=dict(type='number');INT=dict(type='integer')
RANGE=obj(dict(start=NUM,end=NUM))
SCHEMA=obj(dict(segments=arr(obj(dict(start=NUM,end=NUM,category=dict(type='string',enum=list(CATEGORIES)),title=STR,summary=STR,why=STR,
    visual_evidence=STR,frame_times=arr(NUM),cue_ids=arr(INT)))),
    moments=arr(obj(dict(title=STR,summary=STR,category=dict(type='string',enum=['critical','secondary']),why=STR,
        ranges=arr(RANGE),visual_evidence=STR,frame_times=arr(NUM),cue_ids=arr(INT),threads=arr(STR))))))

INSTRUCTION="""Analyze the chronologically timestamped MOVIE FRAMES and optional transcript, never outside knowledge.
Create an actions logbook for this entire batch. There may be NO dialogue: visible actions still matter.
segments must partition batch_start..batch_end continuously in order, with no gaps or overlap.
Describe actual visible actions, goals, discoveries, threats and transitions. Prefer coherent 5-15 second beats;
combine repetitive credits/uneventful shots. Critical = causal action/revelation/goal/threat; secondary = necessary setup;
filler = credits/logos or repetitive material with no new story information; unclassified = genuinely uncertain evidence.
Do not label silent action filler merely because it has no dialogue. Do not invent identities, motivations, relationships,
speech or offscreen actions. Describe people by visible attributes; character identities and connections come from SQLite.
Every segment must cite supplied frame_times INSIDE its interval and describe what those frames show. Reference cue_ids
only when the supplied timed line actually supports the description. Never transfer a later line into an earlier action.
Also propose 0-4 DISTINCT recap moments: each 7-45 seconds TOTAL, normally 7-30 seconds, up to 45 only when causal
setup and payoff require it. Each has 1-4 ordered ranges strictly inside this batch. Cut OUT dead air, irrelevant shots,
repetition, logos, credits and filler BETWEEN important actions. Do not expand a brief action with unrelated padding.
Keep enough setup and outcome to understand the action. No gratuitous montage if continuous action is already clear.
A range cannot overlap a filler/unclassified segment. If an action has no understandable 7-second edit, omit it.
Moment title and summary must be supported entirely by its selected ranges, not by outside or future context.
Use exact supplied frame timestamps as evidence; start/end are approximate editorial boundaries based on sampled frames,
not claims of exhaustive frame-by-frame observation. No invented quotes. Include all required fields. IMPORTANT: verify every cited frame and full cue start/end is INSIDE its segment AND moment ranges. Do not use a frame from the next segment in the previous one. A scene containing dialogue cannot be classified as logos/credits. Every correction must fix all repair_feedback, not repeat the previous answer."""


def normalize_evidence(answer,a,b,times,cues):
    """Resolve mechanical citation/boundary inconsistencies, never invent observations.
    Subtitle-aligned starts may borrow only from an immediately preceding filler
    segment. Citations outside the actual retained footage are discarded and audited.
    """
    answer=copy.deepcopy(answer);lookup={c['id']:c for c in cues};changes=[]
    segments=answer['segments']
    previous=a
    for row in segments:
        if abs(row['start']-previous)<=.15:
            if row['start']!=previous:changes.append(dict(title=row['title'],boundary_rounding_start=previous,previous_start=row['start']))
            row['start']=previous
        previous=row['end']
    if segments and abs(segments[-1]['end']-b)<=.3:
        if segments[-1]['end']!=b:changes.append(dict(title=segments[-1]['title'],boundary_rounding_end=b,previous_end=segments[-1]['end']))
        segments[-1]['end']=b
    for i,row in enumerate(segments):
        cited=[lookup[j] for j in row['cue_ids'] if j in lookup]
        if cited and row['category'] in ('critical','secondary') and i and segments[i-1]['category'] in ('filler','unclassified'):
            start=min(c['start'] for c in cited)
            previous=segments[i-1]
            if previous['start']+2<start<row['start'] and all(c['end']<=row['end']+.001 for c in cited):
                changes.append(dict(title=row['title'],subtitle_aligned_start=start,previous_start=row['start']))
                row['start']=start;previous['end']=start
    for row in segments+answer['moments']:
        ranges=row.get('ranges') or [dict(start=row['start'],end=row['end'])]
        frames=[t for t in row['frame_times'] if any(abs(t-s)<.002 for s in times) and any(r['start']<=t<=r['end'] for r in ranges)]
        ids=[i for i in row['cue_ids'] if i in lookup and any(r['start']<=lookup[i]['start']+.001 and lookup[i]['end']<=r['end']+.001 for r in ranges)]
        if frames!=row['frame_times'] or ids!=row['cue_ids']:changes.append(dict(title=row['title'],discarded_frame_times=[t for t in row['frame_times'] if t not in frames],discarded_cue_ids=[i for i in row['cue_ids'] if i not in ids]))
        row['frame_times']=frames;row['cue_ids']=ids
    return answer,changes


def validate_batch(answer,a,b,times,cues):
    timeline=answer['segments'];previous=a
    def evidence(row,start,end):
        if not row['visual_evidence'].strip() or not row['frame_times']:raise ValueError('Visual evidence and frame timestamps required')
        if any(not any(abs(t-s)<.002 for s in times) or not start<=t<=end for t in row['frame_times']):raise ValueError(f"{row.get('title')!r} interval {start:.3f}..{end:.3f}: frame_times outside interval or not supplied: {[t for t in row['frame_times'] if not any(abs(t-s)<.002 for s in times) or not start<=t<=end]}. Remove these references or correct boundaries; all remaining frames must be inside.")
        allowed={c['id']:c for c in cues}
        if any(i not in allowed or allowed[i]['start']<start-.05 or allowed[i]['end']>end+.05 for i in row['cue_ids']):raise ValueError(f"{row.get('title')!r} interval {start:.3f}..{end:.3f}: cue_ids outside interval: {[(i,allowed.get(i,{}).get('start'),allowed.get(i,{}).get('end')) for i in row['cue_ids'] if i not in allowed or allowed[i]['start']<start-.05 or allowed[i]['end']>end+.05]}. Correct interval boundaries to include the entire cited dialogue or remove unrelated citations.")
    if not timeline:raise ValueError('Missing action coverage')
    for s in timeline:
        start,end=s['start'],s['end']
        if not all(type(t) in (int,float) and math.isfinite(t) for t in (start,end)) or abs(start-previous)>.02 or end<=start or end>b+.02:raise ValueError(f"Action {s['title']!r} starts {start} and ends {end}, but must start exactly at {previous} and end <= {b}. Cover ALL of {a}..{b} continuously, not just first/last sample times.")
        s['start']=previous;s['end']=min(b,end);previous=s['end']
        evidence(s,s['start'],s['end'])
    if abs(previous-b)>.02:raise ValueError(f'Action coverage ended at {previous}, must reach batch end {b}; use the complete source interval, not just the last sampled frame.')
    timeline[-1]['end']=b
    accepted=[]
    for moment in answer['moments']:
        ranges=moment['ranges'];last=a
        if not 1<=len(ranges)<=4:raise ValueError('Use 1-4 action ranges')
        for r in ranges:
            x,y=r['start'],r['end']
            if not all(type(t) in (int,float) and math.isfinite(t) for t in (x,y)) or x<last-.001 or x<a or y>b+.001 or y<=x:raise ValueError('Invalid moment boundaries')
            if any(s['category'] in ('filler','unclassified') and min(y,s['end'])-max(x,s['start'])>.02 for s in timeline):raise ValueError(f"Moment {moment['title']!r} range {x}..{y} overlaps filler/uncertain segments {[(s['start'],s['end'],s['title']) for s in timeline if s['category'] in ('filler','unclassified') and min(y,s['end'])-max(x,s['start'])>.02]}. Correct misclassified action boundaries or trim the edit.")
            last=y
        length=sum(r['end']-r['start'] for r in ranges)
        if not 7-.001<=length<=45+.001:raise ValueError('Moment must be 7-45 seconds total')
        evidence(moment,ranges[0]['start'],ranges[-1]['end'])
        if any(not any(r['start']<=t<=r['end'] for r in ranges) for t in moment['frame_times']):raise ValueError(f"Moment {moment['title']!r} excludes cited frames {[t for t in moment['frame_times'] if not any(r['start']<=t<=r['end'] for r in ranges)]}; extend its ranges within action segments or remove those citations.")
        if any(not any(r['start']<=next(c for c in cues if c['id']==i)['start'] and next(c for c in cues if c['id']==i)['end']<=r['end'] for r in ranges) for i in moment['cue_ids']):raise ValueError('Moment dialogue was cut out')
        for old in accepted:
            overlap=sum(max(0,min(x['end'],y['end'])-max(x['start'],y['start'])) for x in ranges for y in old['ranges'])
            if overlap>.7*min(length,sum(r['end']-r['start'] for r in old['ranges'])):raise ValueError('Duplicate recap moments')
        accepted.append(moment)
    return answer


def finalize_batch(answer,a,b,times,cues):
    answer,changes=normalize_evidence(answer,a,b,times,cues)
    validate_batch(dict(answer,moments=[]),a,b,times,cues)
    accepted=[];omitted=[]
    for moment in answer['moments']:
        try:
            validate_batch(dict(answer,moments=accepted+[moment]),a,b,times,cues)
            accepted.append(moment)
        except (ValueError,KeyError,TypeError,StopIteration) as error:
            omitted.append(dict(title=moment.get('title',''),reason=str(error)))
    # Each meaningful AI-authored beat is also a valid continuous edit. This
    # preserves action context when a proposed montage was too short/invalid.
    # No new story claim or filler padding is introduced by the local editor.
    short_groups=[];group=[]
    for segment in answer['segments']+[dict(start=b,end=b,category='filler')]:
        length=segment['end']-segment['start']
        if segment['category'] in ('critical','secondary') and length<7:
            group.append(segment)
            if sum(x['end']-x['start'] for x in group)<7:continue
            short_groups.append(dict(start=group[0]['start'],end=group[-1]['end'],category='critical' if any(x['category']=='critical' for x in group) else 'secondary',
                title=' / '.join(x['title'] for x in group),summary=' '.join(x['summary'] for x in group),why=' '.join(x['why'] for x in group),
                visual_evidence=' '.join(x['visual_evidence'] for x in group),frame_times=[t for x in group for t in x['frame_times']],cue_ids=[i for x in group for i in x['cue_ids']]))
        group=[]
    for segment in answer['segments']+short_groups:
        length=segment['end']-segment['start']
        if segment['category'] not in ('critical','secondary') or not 7<=length<=45:continue
        candidate={k:copy.deepcopy(segment[k]) for k in ('title','summary','category','why','visual_evidence','frame_times','cue_ids')}
        candidate.update(ranges=[dict(start=segment['start'],end=segment['end'])],threads=[])
        try:
            validate_batch(dict(answer,moments=accepted+[candidate]),a,b,times,cues)
            accepted.append(candidate)
        except ValueError:pass  # Valid proposed edit already covers the same action.
    answer['moments']=accepted
    return answer,changes,omitted


def build(pipeline,m,progress=lambda *args:None):
    """Explicit full-source preparation, resumable per validated batch. Never viewer-triggered."""
    store=Logbook(pipeline.catalog);existing=store.load(m)
    if existing:return existing
    ai=pipeline.ai
    if getattr(ai,'provider','')!='nova':raise AIUnavailable('Frame logbook requires the configured Amazon Nova vision API; text-only providers cannot inspect frames.')
    if not ai.key:raise AIUnavailable('AI unavailable — configure NOVA_API_KEY and rerun tools/build_logbook.py. No annotations were invented.')
    ai.prepare();store.schema();key=source_key(pipeline.catalog,m)
    video,script=pipeline.catalog.inputs(m);duration=pipeline.ffprobe(video)
    cues=read_srt(script,float(m.get('transcript_offset_seconds',0))) if script else []
    folder=pipeline.runtime/'logbook-frames'/m['id']/key[:20]
    # About 2 seconds between images; <=20 source frames per multimodal request.
    count=max(1,math.ceil(duration/38));boundaries=[0.]
    for i in range(1,count):
        point=round(duration*i/count,3)
        crossing=next((c for c in cues if c['start']<point<c['end']),None)
        if crossing:point=round(min(duration,crossing['end']),3)
        if point>boundaries[-1]:boundaries.append(point)
    boundaries.append(duration);timeline=[];events=[]
    for n,(a,b) in enumerate(zip(boundaries,boundaries[1:])):
        with sqlite3.connect(store.db) as c:cached=c.execute('SELECT payload FROM action_logbook_batch WHERE unit_id=? AND source_key=? AND batch_index=?',(m['id'],key,n)).fetchone()
        if cached:
            batch=json.loads(cached[0]);answer=batch['answer']
        else:
            size=min(20,max(2,math.ceil((b-a)/2)))
            times=[round(a+.05+(b-a-.3)*i/(size-1),3) for i in range(size)]
            selected=[c for c in cues if a<=c['start'] and c['end']<=b]
            progress('frames',f"{m['id']} · actions {n+1}/{len(boundaries)-1} · {a:.1f}–{b:.1f}s")
            data=dict(unit_id=m['id'],batch_start=a,batch_end=b,source_offset_seconds=m.get('original_start_seconds',0),
                transcript=selected,frame_times=times,sampling_note='Approx. two-second samples; do not claim unseen exact motion.',
                _images=[dict(label=f'SOURCE FRAME local time {t:.3f}s / original {t+float(m.get("original_start_seconds",0)):.3f}s',url=frame_image(video,folder,t)) for t in times])
            errors=[]
            for attempt in range(4):
                answer=ai.json('actions_logbook',INSTRUCTION,dict(data,repair_feedback=errors),SCHEMA,progress,max_tokens=4500)
                try:
                    answer,changes,omitted=finalize_batch(answer,a,b,times,selected)
                    if changes or omitted:ai.log(dict(task='actions_logbook_validation',status='evidence_normalized',unit=m['id'],batch=n,changes=changes,omitted_moments=omitted))
                    break
                except (ValueError,KeyError,TypeError,StopIteration) as error:
                    errors.append(str(error));ai.log(dict(task='actions_logbook_validation',status='repair',unit=m['id'],batch=n,error=str(error)))
                    ai.invalidate('actions_logbook')
            else:raise ValueError('Action logbook validation failed: '+'; '.join(errors))
            batch=dict(answer=answer,frame_times=times,provider=ai.label,model=ai.model)
            with sqlite3.connect(store.db) as c:c.execute('INSERT OR REPLACE INTO action_logbook_batch VALUES (?,?,?,?)',(m['id'],key,n,json.dumps(batch)))
        for j,s in enumerate(answer['segments']):
            timeline.append(dict(s,basis='sampled_visual',evidence_cue_ids=s['cue_ids']))
        for j,e in enumerate(answer['moments']):
            events.append(dict(id=f'action_{n:03d}_{j:02d}',title=e['title'],summary=e['summary'],category=e['category'],why=e['why'],
                ranges=e['ranges'],start=e['ranges'][0]['start'],end=e['ranges'][-1]['end'],characters=[],relationships=[],
                threads=e['threads'],visual_scene=True,location='Not stated',evidence=e['visual_evidence'],frame_times=e['frame_times'],
                evidence_cue_ids=e['cue_ids'],anchor_cue_ids=e['cue_ids']))
    result=dict(version=11,logbook_version=VERSION,source_key=key,id=m['id'],title=m['title'],duration=duration,events=events,timeline=timeline,
        provider=ai.label,model=ai.model,analysis_origin='Persistent AI action logbook · sampled movie frames'+(' and timed transcript' if script else ' · no transcript'),
        created_at=datetime.now(timezone.utc).isoformat(),sampling_interval_seconds=2,boundary_precision='Approximate, based on timestamped sampled frames',persistent_logbook=True)
    offset=float(m.get('original_start_seconds',0))
    with sqlite3.connect(store.db) as c:
        c.execute('INSERT OR REPLACE INTO action_logbook_run VALUES (?,?,?,?,?,?,?,?)',(m['id'],key,VERSION,ai.label,ai.model,result['created_at'],duration,json.dumps(result)))
        c.execute('DELETE FROM action_logbook WHERE unit_id=? AND source_key=?',(m['id'],key))
        c.executemany('INSERT INTO action_logbook VALUES (?,?,?,?,?,?,?,?,?,?,?)',[(m['id'],key,f'beat_{i:04d}',s['start'],s['end'],offset+s['start'],offset+s['end'],s['category'],s['title'],s['summary'],json.dumps(dict(frame_times=s['frame_times'],cue_ids=s['cue_ids'],visual_evidence=s['visual_evidence'],why=s['why']))) for i,s in enumerate(timeline)])
    return result


def recommend_stored(pipeline,m,analysis,folder,state,candidates,progress):
    """Personalize already AI-authored edits locally: no LLM/key required."""
    forgotten=[e for e in analysis['events'] if state['memory'].get(e['id'])==0]
    threads={t for e in forgotten for t in e['threads']};focus=forgotten[-1] if forgotten else None
    def rank(e):
        relation=len(set(e['threads'])&threads)*4
        distance=abs(e['start']-focus['start']) if focus else e['start']
        return (-relation,-(2 if e['category']=='critical' else 1),distance,e['start'])
    items=[];used=0;history=state.get('clip_history',[])
    for e in sorted(candidates,key=rank):
        ranges=e['ranges'];length=sum(r['end']-r['start'] for r in ranges)
        if not 7-.001<=length<=45+.001 or used+length>state['budget']:continue
        if not covers(windows(m),e['start'],e['end']) or not all(covers(windows(m),r['start'],r['end']) for r in ranges):continue
        if any(sum(max(0,min(a['end'],b['end'])-max(a['start'],b['start'])) for a in ranges for b in old['ranges'])>.7*min(length,sum(r['end']-r['start'] for r in old['ranges'])) for old in history+items):continue
        reason=e['why']+(' Related context for a moment you did not remember.' if focus and set(e['threads'])&threads else '')
        progress('editing','Assembling stored action moments · '+e['title'])
        items.append(dict(event_id=e['id'],ranges=copy.deepcopy(ranges),duration=round(length,3),reason=reason,
            clip=pipeline.render(pipeline.catalog.inputs(m)[0],folder,ranges,progress)))
        used+=length
        if len(items)>=5:break
    return dict(items=items,total_seconds=round(used,3),explanation='Reused the saved AI visual logbook. Ranked unseen, unremembered edits by related story threads, plot importance and source proximity; respected character/relationship filters and excluded repeated footage.',
        model=analysis['model'],provider=analysis['provider'],selection_mode='local ranking of persistent AI edits')
