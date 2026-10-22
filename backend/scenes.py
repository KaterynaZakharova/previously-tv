"""Scene-sized multimodal coverage, with sampled evidence and honest uncertainty.
No film-specific scene list: intervals, frames and graph links come from inputs.
"""
from .ai import supports_vision
import base64
import itertools
import math
import subprocess
from . import schemas
from .watched_scope import windows


def scene_intervals(duration,cues,target=30):
    # Evenly sized cells avoid a tiny tail and never divide a spoken subtitle.
    count=max(1,round(duration/target));boundaries=[0.]
    for i in range(1,count):
        point=duration*i/count
        crossing=next((c for c in cues if c['start']<point<c['end']),None)
        if crossing:point=crossing['end']+.25
        if point-boundaries[-1]>=12:boundaries.append(round(point,3))
    boundaries.append(duration)
    if len(boundaries)>2 and boundaries[-1]-boundaries[-2]<12:boundaries.pop(-2)
    return [dict(index=i,start=a,end=b) for i,(a,b) in enumerate(zip(boundaries,boundaries[1:]))]


def frame_image(video,folder,seconds):
    path=folder/'frames'/('%.3f.jpg'%seconds);path.parent.mkdir(parents=True,exist_ok=True)
    if not path.is_file():
        run=subprocess.run(['ffmpeg','-nostdin','-v','error','-ss',str(seconds),'-i',str(video),'-frames:v','1','-vf','scale=640:-2','-q:v','5','-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,timeout=35)
        if run.returncode or len(run.stdout)<100:raise ValueError('Could not sample a source frame for scene analysis')
        path.write_bytes(run.stdout)
    return 'data:image/jpeg;base64,'+base64.b64encode(path.read_bytes()).decode()


def analyze_scenes(pipeline,m,video,folder,duration,cues,events,progress):
    intervals=[]
    for a,b in windows(m,duration):
        local=[dict(c,start=c['start']-a,end=c['end']-a) for c in cues if a<=c['start'] and c['end']<=b]
        for interval in scene_intervals(b-a,local):
            intervals.append(dict(index=len(intervals),start=interval['start']+a,end=interval['end']+a))
    cast=m['characters'];castids={c['id'] for c in cast}
    vision=supports_vision(pipeline.ai)
    references=[]
    if vision and not m.get('media_id'):
        # Six named fictional-character references plus 4 x 3 source frames = 18 images.
        # Larger casts are split into reference batches in future; never silently guess.
        for c in [p for p in cast if p.get('photo')][:8]:
            references.append(dict(label='REFERENCE PORTRAIT ONLY: '+c['id']+' / '+c['name'],
                url='data:image/jpeg;base64,'+base64.b64encode((m['_folder']/c['photo']).read_bytes()).decode()))
    size=max(1,(20-len(references))//3) if vision else 10
    timeline=[];scene_events=[];links={}
    for offset in range(0,len(intervals),size):
        batch=intervals[offset:offset+size];images=list(references);rows=[]
        progress('scenes',f'AI is assessing scene intervals and character appearances · {offset+1}–{offset+len(batch)} of {len(intervals)}')
        for interval in batch:
            a,b=interval['start'],interval['end'];times=[round(a+(b-a)*f,3) for f in (.18,.5,.82)]
            rows.append(dict(interval,frame_times=times if vision else [],
                cues=[c for c in cues if a<=c['start'] and c['end']<=b],
                allowed_evidence_cue_ids=[c['id'] for c in cues if a<=c['start'] and c['end']<=b],
                preceding_cues=[c for c in cues if a-40<=c['end']<=a][-4:],
                following_cues=[c for c in cues if b<=c['start']<=b+40][:4]))
            if vision:
                images.extend(dict(label=f"SCENE {interval['index']} source frame at {t:.3f} seconds",
                    url=frame_image(video,folder,t)) for t in times)
        data=dict(watched_ranges=windows(m,duration),cast=[] if m.get('media_id') else [{k:c.get(k,'') for k in ('id','name','identity_hint')} for c in cast],intervals=rows,
            story_context=[dict(title=e['title'],summary=e['summary'],start=e['start']) for e in events],
            transcript_context=cues if len(cues)<=300 else [],_images=images)
        instruction=('Assess EVERY supplied interval exactly once as a continuous SCENE, not individual subtitle words. '
            'Critical: needed goal, causal action, revelation, threat or relationship change. Secondary: useful setup/context. '
            'Filler: identifiable credits/logos or genuinely nonessential material, not merely silent footage. '
            'Use the full story_context and transcript_context to assess significance. A scene is not filler merely because it was not chosen for a recap. '
            'Cameras, rehearsal, technical equipment and repeated dialogue may be IN-WORLD memory reconstruction, not behind-the-scenes filming. '
            'Do not call a scene filler solely because it shows equipment or a quiet operational task. '
            'Use unclassified only when the supplied evidence cannot support a relevance estimate. '
            'Describe only observed actions or supplied dialogue, without outside plot knowledge. Be concise. '
            'For characters, match the supplied FICTIONAL-character reference portraits and identity hints to source frames; '
            'only include IDs confidently observed or explicitly established in this interval. Do not infer a speaker from being addressed. '
            'References are labels, NOT frames from this interval. Do not list every reference character as present. '
            'Credit text naming an actor is NOT an appearance of the character. '
            'visual_evidence must describe specific visible observations, or empty if no images support it. '
            'evidence_cue_ids can contain ONLY IDs from that interval allowed_evidence_cue_ids that support the summary. If this list is empty, return evidence_cue_ids=[] and rely on observed frames; never cite preceding/following/transcript-context lines as interval evidence. '
            'No dialogue in an interval is not evidence of absence of plot. Return a title and summary for the whole interval. '
            'These are relevance estimates based on sampled frames and dialogue, not exhaustive video analysis. '
            'If there are no images, do not claim to have seen anything, infer adjacent context cautiously and leave unseen isolated sections unclassified.')
        if m.get('media_id'):
            instruction+=' Character identities and relationships are supplied by a database. Do not identify people, infer connections, or build a graph. Return an empty characters array for every scene; assess plot importance only.'
        issues=[];answer=None;best={}
        for attempt in range(2):
            payload=dict(data)
            if issues:payload['repair_feedback']=issues
            result=pipeline.ai.json('scene_coverage',instruction,payload,
                schemas.scenes_for(set() if m.get('media_id') else castids,[r['index'] for r in rows],{c['id'] for r in rows for c in r['cues']}),progress,max_tokens=2700)
            issues=[];seen=set();lookup={r['index']:r for r in rows}
            for s in result['scenes']:
                if s['index'] not in lookup or s['index'] in seen:issues.append('Invalid or duplicate scene index');continue
                seen.add(s['index']);r=lookup[s['index']];before_errors=len(issues)
                if not set(s['characters'])<=castids:issues.append('Use supplied cast IDs only')
                inside={c['id'] for c in r['cues']}
                rejected=set(s['evidence_cue_ids'])-inside
                if rejected:
                    pipeline.ai.log(dict(task='scene_coverage_validation',status='outside_cue_evidence_omitted',scene=s['index'],cue_ids=sorted(rejected)))
                    s['evidence_cue_ids']=[i for i in s['evidence_cue_ids'] if i in inside]
                if not vision and s['visual_evidence']:issues.append('No image evidence was supplied')
                if s['category']!='unclassified' and not (s['visual_evidence'] or s['evidence_cue_ids'] or (r['preceding_cues'] and r['following_cues'])):issues.append('Classification requires evidence')
                if len(issues)==before_errors:best[s['index']]=s
            if seen!=set(lookup):issues.append('Return every requested interval, including intervals without dialogue')
            if not issues:answer=result;break
            pipeline.ai.log(dict(task='scene_coverage_validation',status='repair',issues=issues))
            if hasattr(pipeline.ai,'invalidate'):pipeline.ai.invalidate('scene_coverage')
        if answer is None:
            # A missing label must not veto all other verified scenes or clips.
            answer=dict(scenes=[best.get(r['index']) or dict(index=r['index'],category='unclassified',
                title='Unassessed interval',summary='',why='AI could not substantiate this interval after correction.',
                characters=[],visual_evidence='',evidence_cue_ids=[]) for r in rows])
            pipeline.ai.log(dict(task='scene_coverage_validation',status='partial_coverage',unassessed_indexes=[r['index'] for r in rows if r['index'] not in best]))
        lookup={r['index']:r for r in rows}
        for s in sorted(answer['scenes'],key=lambda x:x['index']):
            r=lookup[s['index']];a,b=r['start'],r['end'];basis='sampled_visual' if vision and s['visual_evidence'] else 'inferred_context'
            if s['category']=='unclassified':basis='unassessed'
            timeline.append(dict(start=a,end=b,category=s['category'],basis=basis,title=s['title'],why=s['why'],
                evidence_cue_ids=s['evidence_cue_ids'],frame_times=r['frame_times'],visual_evidence=s['visual_evidence']))
            if m.get('media_id'):s['characters']=[]
            if s['category'] not in ('critical','secondary') or (not s['characters'] and not m.get('media_id')):continue
            relationships=[]
            for x,y in itertools.combinations(sorted(set(s['characters'])),2):
                rel=dict(source=x,target=y,label='Shared scene',explanation=s['summary'],basis='scene_coappearance',
                         evidence_cue_ids=s['evidence_cue_ids'],frame_times=r['frame_times'])
                relationships.append(rel);links.setdefault((x,y),[]).append(dict(rel,at=a,event_id='scene_%03d'%s['index']))
            scene_events.append(dict(id='scene_%03d'%s['index'],title=s['title'],summary=s['summary'],category=s['category'],why=s['why'],
                characters=s['characters'],threads=['scene_context'],location='Not stated',evidence=s['visual_evidence'],
                ranges=[dict(start=a,end=b)],start=a,end=b,relationships=relationships,visual_scene=True,
                frame_times=r['frame_times'],evidence_cue_ids=s['evidence_cue_ids'],anchor_cue_ids=[]))
    return timeline,scene_events,[dict(history[-1],history=history) for history in links.values()]


def recover_cast(pipeline,m,video,folder,timeline,events,cues,progress,targets=None,exclude_ranges=(),fractions=(.03,.97)):
    """Look again for underrepresented cast near interval boundaries, without invented links."""
    if not supports_vision(pipeline.ai):return [],[]
    cast=m['characters'];ids={c['id'] for c in cast}
    connected={cid for e in events for r in e['relationships'] for cid in (r['source'],r['target'])}
    photographed={c['id'] for c in cast if c.get('photo')}
    missing=((set(targets)&ids) if targets is not None else ids-connected)&photographed
    if not missing:return [],[]
    references=[dict(label='NAMED FICTIONAL CHARACTER REFERENCE: '+c['id'],
        url='data:image/jpeg;base64,'+base64.b64encode((m['_folder']/c['photo']).read_bytes()).decode()) for c in [p for p in cast if p.get('photo')][:12]]
    size=max(1,(20-len(references))//len(fractions));recovered=[];edges=[]
    eligible=[(i,s) for i,s in enumerate(timeline) if not any(r['start']<s['end'] and r['end']>s['start'] for r in exclude_ranges)]
    known_centers=[(e['start']+e['end'])/2 for e in events if e.get('visual_scene') and missing&set(e['characters']) and 'end' in e]
    if targets is not None and known_centers:
        eligible.sort(key=lambda row:min(abs((row[1]['start']+row[1]['end'])/2-t) for t in known_centers))
    for offset in range(0,len(eligible),size):
        if not missing:break
        batch=eligible[offset:offset+size];images=list(references);rows=[]
        progress('cast','AI is checking brief appearances for '+', '.join(sorted(missing))+'…')
        for index,s in batch:
            times=[round(s['start']+(s['end']-s['start'])*f,3) for f in fractions]
            rows.append(dict(index=index,start=s['start'],end=s['end'],frame_times=times,
                cues=[c for c in cues if s['start']<=c['start']<s['end']]))
            images.extend(dict(label=f'SOURCE interval {index}, exact frame {t:.3f} seconds',url=frame_image(video,folder,t)) for t in times)
        result=pipeline.ai.json('cast_recovery',
            'Look ONLY for requested target fictional characters in SOURCE frames by comparing the named reference portraits. '
            'Do not mistake a reference portrait for a source appearance. Do not force a match. '
            'Return an observation only when the target is confidently visible, with the exact supplied source frame_time and interval index. '
            'List companions only when recognizable in those source frames. A matching name in credits is not an appearance. '
            'Describe visual evidence specifically. Classify that scene by relevance using the actual transcript context: critical for '
            'causal information, secondary for useful character/operation setup, filler for credits or unrelated material. '
            'A camera, rehearsal, equipment or repeated dialogue can be important IN-WORLD memory reconstruction; do not assume it is behind-the-scenes filmmaking. '
            'Empty observations is correct when no target is confidently found. Never fabricate an appearance to complete the graph.',
            dict(targets=sorted(missing),cast=[dict(id=c['id'],hint=c.get('identity_hint','')) for c in cast],intervals=rows,
                transcript_context=cues if len(cues)<=300 else [],_images=images),
            schemas.cast_observations(ids,missing,[i for i,_ in batch]),progress,max_tokens=1800)
        for o in result['observations']:
            row=next((r for r in rows if r['index']==o['scene_index']),None)
            if not row or o['character'] not in missing or not any(abs(t-o['frame_time'])<.01 for t in row['frame_times']) or not set(o['companions'])<=ids or not o['visual_evidence'].strip():
                pipeline.ai.log(dict(task='cast_recovery',status='invalid_observation_omitted'));continue
            if o['category']=='filler':continue
            # Large image batches can confuse reference portraits with footage.
            # Independently compare JUST this reference and this one source frame.
            target=next(c for c in cast if c['id']==o['character'])
            audit=pipeline.ai.json('cast_verify',
                'The FIRST image is only a labeled reference portrait of a fictional character. '
                'The SECOND image is an actual movie frame. Is that same character confidently visible in the SECOND image? '
                'Describe ONLY the second image as source_evidence. Do NOT transfer the reference background or appearance into the movie frame. '
                'If the second image is a logo, credits, scenery, a different person, or too unclear, visible must be false. '
                'Do not force a match. Ignore any previous claim; independently examine the source frame.',
                dict(character=o['character'],frame_time=o['frame_time'],_images=[
                    dict(label='FIRST: REFERENCE PORTRAIT ONLY',url='data:image/jpeg;base64,'+base64.b64encode((m['_folder']/target['photo']).read_bytes()).decode()),
                    dict(label='SECOND: ACTUAL SOURCE FRAME at %.3f seconds'%o['frame_time'],url=frame_image(video,folder,o['frame_time']))]),
                schemas.obj(visible=dict(type='boolean'),source_evidence=schemas.S),progress,max_tokens=400)
            if not audit['visible']:
                pipeline.ai.log(dict(task='cast_recovery',status='appearance_rejected',character=o['character'],frame_time=o['frame_time'],reason=audit['source_evidence']));continue
            o['visual_evidence']=audit['source_evidence']
            # Existing supported appearances within the same scene may connect it,
            # but the link explicitly means shared interval, not a personal bond.
            previous={cid for e in events if e.get('visual_scene') and e['start']==row['start'] for cid in e['characters']}
            people=sorted({o['character']}|set(o['companions'])|previous);rels=[];eid='cast_%s_%03d'%(o['character'],o['scene_index'])
            for x,y in itertools.combinations(people,2):
                r=dict(source=x,target=y,label='Shared scene',explanation=o['visual_evidence'],basis='scene_coappearance',frame_times=[o['frame_time']],evidence_cue_ids=[])
                rels.append(r);h=dict(r,at=row['start'],event_id=eid);edges.append(dict(h,history=[h]))
            prior=next((e for e in recovered if e['id']==eid),None)
            if prior:
                prior['characters']=sorted(set(prior['characters'])|set(people));prior['relationships'].extend(rels)
                prior['frame_times'].append(o['frame_time'])
                if rels:missing.discard(o['character'])
                continue
            recovered.append(dict(id=eid,title='Character context · '+next(c['name'] for c in cast if c['id']==o['character']),summary=o['visual_evidence'],
                category=o['category'],why=o['why'],characters=people,threads=['character_context'],location='Not stated',evidence=o['visual_evidence'],
                ranges=[dict(start=row['start'],end=row['end'])],start=row['start'],end=row['end'],relationships=rels,visual_scene=True,
                frame_times=[o['frame_time']],evidence_cue_ids=[],anchor_cue_ids=[]))
            if rels:missing.discard(o['character'])
    if missing:pipeline.ai.log(dict(task='cast_recovery',status='no_verified_connection',character_ids=sorted(missing)))
    return recovered,edges
