"""One-time caption/edit review against ONLY the retained footage, cached in SQLite."""
import copy,hashlib,json,math,os,sqlite3
from datetime import datetime,timezone
from .ai import Nova,AIUnavailable
from .catalog import read_srt
from .logbook import obj,arr,STR,NUM,INT,RANGE,CATEGORIES
from .scenes import frame_image
from .watched_scope import covers

REVIEW_SCHEMA=obj(dict(items=arr(obj(dict(index=INT,title=STR,summary=STR,why=STR,category=dict(type='string',enum=list(CATEGORIES)),
    visual_evidence=STR,frame_times=arr(NUM),cue_ids=arr(INT),ranges=arr(RANGE),observations=arr(obj(dict(frame_time=NUM,description=STR))))))))

def validate(row,original,times,cues):
    ranges=row['ranges'];bounds=[[r['start'],r['end']] for r in original['ranges']]
    if not 1<=len(ranges)<=4:raise ValueError('Use 1-4 retained ranges')
    previous=-1
    for r in ranges:
        a,b=r['start'],r['end']
        if not all(type(t) in (int,float) and math.isfinite(t) for t in (a,b)) or a<previous or b<=a or not covers(bounds,a,b):raise ValueError('Review cut escaped the original supported edit')
        previous=b
    length=sum(r['end']-r['start'] for r in ranges)
    if not 7-.001<=length<=45+.001:raise ValueError('Reviewed edit must total 7-45 seconds')
    if not row['visual_evidence'].strip() or not row['frame_times']:raise ValueError('Review requires specific visual evidence')
    if any(not any(abs(t-x)<.002 for x in times) or not any(r['start']<=t<=r['end'] for r in ranges) for t in row['frame_times']):raise ValueError('Review cites a frame outside the retained footage')
    lookup={c['id']:c for c in cues}
    if any(i not in lookup or not any(r['start']<=lookup[i]['start'] and lookup[i]['end']<=r['end'] for r in ranges) for i in row['cue_ids']):raise ValueError('Review cites dialogue outside the retained footage')
    if 'observations' in row:
        observed=[x['frame_time'] for x in row['observations']]
        if len(observed)!=len(set(observed)) or set(observed)!=set(t for t in times if any(r['start']<=t<=r['end'] for r in ranges)):raise ValueError(f"For clip {row['index']}, observations must describe each of these exact times once: {[t for t in times if any(r['start']<=t<=r['end'] for r in ranges)]}. Got {observed}.")
    return row

def refine(pipeline,m,stored,progress=lambda *args:None):
    if stored.get('visual_caption_review')==2:return stored
    if not pipeline.ai.key:raise AIUnavailable('A Nova key is needed for the one-time caption review of this new logbook.')
    ai=Nova(pipeline.runtime,key=pipeline.ai.key,model=os.getenv('NOVA_REVIEW_MODEL','nova-pro-v1'))
    db=pipeline.catalog.root/'assets/db.sqlite'
    with sqlite3.connect(db) as c:c.execute('CREATE TABLE IF NOT EXISTS action_logbook_caption (unit_id TEXT,source_key TEXT,edit_key TEXT,payload TEXT,PRIMARY KEY(unit_id,source_key,edit_key))')
    video,script=pipeline.catalog.inputs(m);cues=read_srt(script) if script else []
    folder=pipeline.runtime/'caption-frames'/m['id']/stored['source_key'][:20];reviewed=[];omitted=[]
    events=stored['events']
    for offset in range(0,len(events),1):
        batch=events[offset:offset+1];rows=[];images=[];lookup={};cached={}
        for i,e in enumerate(batch):
            # Six fresh frames per clip plus segment starts for montage edits.
            total=sum(r['end']-r['start'] for r in e['ranges']);times=[]
            for j in range(6):
                elapsed=.05+(total-.3)*j/5
                for r in e['ranges']:
                    if elapsed<=r['end']-r['start']:
                        times.append(round(r['start']+elapsed,3));break
                    elapsed-=r['end']-r['start']
            times=sorted(set(times+[round(r['start']+.05,3) for r in e['ranges']]))
            selected=[c for c in cues if any(r['start']<=c['start'] and c['end']<=r['end'] for r in e['ranges'])]
            key=hashlib.sha256(json.dumps(dict(version=2,ranges=e['ranges']),sort_keys=True).encode()).hexdigest()
            with sqlite3.connect(db) as c:hit=c.execute('SELECT payload FROM action_logbook_caption WHERE unit_id=? AND source_key=? AND edit_key=?',(m['id'],stored['source_key'],key)).fetchone()
            lookup[i]=(e,times,selected,key)
            if hit:cached[i]=json.loads(hit[0]);continue
            rows.append(dict(index=i,ranges=e['ranges'],frame_times=times,transcript=selected))
            images.extend(dict(label=f'CLIP {i} · actual retained source frame {t:.3f}s',url=frame_image(video,folder,t)) for t in times)
        if rows:
            progress('caption_review',f"{m['id']} · checking retained footage {offset+1}–{min(offset+1,len(events))}/{len(events)}")
            errors=[]
            schema=copy.deepcopy(REVIEW_SCHEMA)
            schema['properties']['items'].update(minItems=0,maxItems=len(rows))
            schema['properties']['items']['items']['properties']['index']['enum']=[r['index'] for r in rows]
            for attempt in range(3):
                answer=ai.json('action_clip_review',
                    'Write literal, specific titles and summaries for EACH supplied clip from ONLY its timestamped source frames and its optional dialogue. '
                    'No outside plot knowledge or earlier/later context. Clips may contain several shots: describe the action actually INSIDE retained ranges. '
                    'Do not label a clip after a shot absent from its frames. Do not invent a mission, briefing, memory process, identity, motivation or relationship. '
                    'Describe visible behavior rather than inferred intent. Dialogue may establish causal facts when it actually says them. '
                    'Classify importance: critical for causal action/revelation/threat/goal, secondary for necessary context, filler for irrelevant/repetitive shots or credits, unclassified if uncertain. '
                    'Keep or tighten original ranges, 1-4 ordered ranges, total 7-45s (prefer 7-30). Cut incidental footage only while setup/action/outcome survive. '
                    'Never extend beyond the supplied ranges or use a frame/cue cut out of your returned ranges. '
                    'If there is no useful action/context, mark filler. Cite exact supplied frame_times and local cue_ids. '
                    'FIRST describe EVERY supplied frame that remains in your returned edit individually in observations (frame_time and concrete description). Use all of those observations to write the clip title/summary; do not ignore later shots. Never describe a human with a gun as a flying machine, and do not carry the first shot description across unrelated later shots. visual_evidence must describe concrete visible observations, not just repeat timestamp labels. Striking visuals/establishing setting alone are secondary, not critical. '
                    'Return the requested index exactly once, or items=[] if this footage has no useful recap action/context. Correct all repair_feedback if provided.',
                    dict(unit_id=m['id'],clips=rows,repair_feedback=errors,_images=images),schema,progress,max_tokens=4500)
                try:
                    if not answer['items']:
                        got={r['index']:dict(omitted=True,reason='The vision reviewer returned no useful recap action/context for this retained edit.') for r in rows}
                        cached.update(got)
                        ai.log(dict(task='action_clip_review_validation',status='empty_edit_omitted',unit=m['id'],indexes=list(got)))
                        break
                    got={r['index']:r for r in answer['items']}
                    if set(got)!={r['index'] for r in rows} or len(got)!=len(answer['items']):raise ValueError('Missing/duplicate clip index')
                    for i,row in got.items():
                        e,times,selected,key=lookup[i]
                        before=dict(frame_times=row['frame_times'][:],cue_ids=row['cue_ids'][:])
                        row['frame_times']=[t for t in row['frame_times'] if any(abs(t-x)<.002 for x in times) and any(r['start']<=t<=r['end'] for r in row['ranges'])]
                        row['cue_ids']=[j for j in row['cue_ids'] if any(c['id']==j and any(r['start']<=c['start'] and c['end']<=r['end'] for r in row['ranges']) for c in selected)]
                        if before!=dict(frame_times=row['frame_times'],cue_ids=row['cue_ids']):ai.log(dict(task='action_clip_review_validation',status='outside_citations_removed',unit=m['id'],index=i,original=before,retained=dict(frame_times=row['frame_times'],cue_ids=row['cue_ids'])))
                        validate(row,e,times,selected)
                    cached.update(got);break
                except (ValueError,KeyError,TypeError) as error:
                    errors.append(str(error));ai.log(dict(task='action_clip_review_validation',status='repair',unit=m['id'],error=str(error)));ai.invalidate('action_clip_review')
            else:raise ValueError('Retained-footage review failed: '+'; '.join(errors))
            with sqlite3.connect(db) as c:
                for i,row in got.items():c.execute('INSERT OR REPLACE INTO action_logbook_caption VALUES (?,?,?,?)',(m['id'],stored['source_key'],lookup[i][3],json.dumps(row)))
        for i,e in enumerate(batch):
            row=cached[i]
            if row.get('omitted'):
                omitted.append(dict(event_id=e['id'],category='omitted',why=row['reason']));continue
            if row['category'] not in ('critical','secondary'):
                omitted.append(dict(event_id=e['id'],category=row['category'],why=row['why']));continue
            item=copy.deepcopy(e)
            item.update({k:copy.deepcopy(row[k]) for k in ('title','summary','category','why','ranges','frame_times')})
            item['frame_observations']=row.get('observations',[])
            item.update(start=row['ranges'][0]['start'],end=row['ranges'][-1]['end'],evidence=row['visual_evidence'],evidence_cue_ids=row['cue_ids'],anchor_cue_ids=row['cue_ids'])
            reviewed.append(item)
    result=dict(stored,updated_at=datetime.now(timezone.utc).isoformat(),events=reviewed,visual_caption_review=2,caption_model=ai.model,omitted_after_caption_review=omitted)
    with sqlite3.connect(db) as c:c.execute('UPDATE action_logbook_run SET payload=? WHERE unit_id=? AND source_key=?',(json.dumps(result),m['id'],stored['source_key']))
    return result
