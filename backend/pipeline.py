"""Autonomous transcript -> evidence graph -> personalized edit decisions -> FFmpeg.
Persistent source-hashed AI action logbooks are reused when available. No identity guessing or synthetic film facts.
"""
import copy
import hashlib
import json
import math
import re
import unicodedata
import subprocess
import threading
from pathlib import Path
from .catalog import read_srt
from .scenes import analyze_scenes,recover_cast
from .story_map import tag_events
from .watched_scope import windows,scoped_cues,clamp_ranges,scope_events,scope_timeline,covers,context_cues
from .logbook import Logbook,recommend_stored
from . import schemas
from .ai import AIUnavailable, AIResponseInvalid

CATEGORIES={'critical','secondary','filler','unclassified'}

def evidence_words(text):
    """Ignore quote wrappers, line joins and punctuation; never add/remove words."""
    text=unicodedata.normalize('NFKC',str(text)).replace('’', "'").replace('‘', "'")
    return re.findall(r"[^\W_]+(?:'[^\W_]+)*",text.casefold(),flags=re.UNICODE)

def quote_is_supported(quote,source):
    wanted=evidence_words(quote);available=evidence_words(source)
    return bool(wanted) and any(available[i:i+len(wanted)]==wanted for i in range(len(available)-len(wanted)+1))

def quote_cue_ids(quote,cues):
    wanted=evidence_words(quote);words=[];owners=[]
    for c in cues:
        tokens=evidence_words(c['text']);words.extend(tokens);owners.extend([c['id']]*len(tokens))
    for i in range(len(words)-len(wanted)+1):
        if wanted and words[i:i+len(wanted)]==wanted:return sorted(set(owners[i:i+len(wanted)]))
    return []

def timed_cue(c):
    def stamp(seconds):
        ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);sec,ms=divmod(ms,1000)
        return '%02d:%02d:%02d.%03d' % (h,m,sec,ms)
    return dict(c,start_timecode=stamp(c['start']),end_timecode=stamp(c['end']))

def context_window(event,cues,target_seconds=22):
    """Propose bounded dialogue context; AI still selects the final cue cuts."""
    positions={c['id']:i for i,c in enumerate(cues)};windows=[]
    for original in event['ranges']:
        a,b=positions[original['first']],positions[original['last']]
        while cues[b]['end']-cues[a]['start']<target_seconds:
            options=[]
            if a>0 and cues[a]['start']-cues[a-1]['end']<=35:
                options.append((cues[a]['start']-cues[a-1]['start'],a-1,b))
            if b+1<len(cues) and cues[b+1]['start']-cues[b]['end']<=35:
                options.append((cues[b+1]['end']-cues[b]['end']+.5,a,b+1))
            options=[x for x in options if cues[x[2]]['end']-cues[x[1]]['start']<=90]
            if not options:break
            _,a,b=min(options)
        if windows and (a<=positions[windows[-1]['last']] or (a==positions[windows[-1]['last']]+1 and cues[a]['start']-windows[-1]['end']<=12)) and cues[b]['end']-windows[-1]['start']<=90:
            windows[-1]['last']=max(windows[-1]['last'],cues[b]['id'])
            windows[-1]['end']=max(windows[-1]['end'],cues[b]['end']+.2)
        else:windows.append(dict(first=cues[a]['id'],last=cues[b]['id'],start=max(0,cues[a]['start']-.16),end=cues[b]['end']+.2))
    return windows

def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

def chunks(cues,limit=4200,max_span=270):
    group=[];size=0
    for c in cues:
        cost=len(c['text'])+55
        if group and (size+cost>limit or c['start']-group[0]['start']>max_span):
            yield group;group=[];size=0
        group.append(c);size+=cost
    if group:yield group

def ranges_to_edl(ranges,cues,allowed=None):
    if not isinstance(ranges,list) or not 1<=len(ranges)<=4:raise ValueError('AI must choose 1–4 transcript ranges')
    byid={c['id']:c for c in cues};result=[];prev=0
    for r in ranges:
        a,b=r['first'],r['last']
        if type(a) is not int or type(b) is not int or a not in byid or b not in byid or b<a or a<=prev:
            raise ValueError('Invalid or out-of-order evidence cue ID')
        if any(i not in byid for i in range(a,b+1)):raise ValueError('Evidence outside transcript chunk')
        if allowed and not any(a>=p['first'] and b<=p['last'] for p in allowed):raise ValueError('Refined edit escaped its evidence')
        start=max(0,byid[a]['start']-.16);end=byid[b]['end']+.2
        result.append(dict(first=a,last=b,start=round(start,3),end=round(end,3)))
        prev=b
    # Subtitle overlap can produce overlapping cuts; merge only adjacent evidence.
    merged=[]
    for r in result:
        if merged and r['start']<=merged[-1]['end']:
            merged[-1]['last']=r['last'];merged[-1]['end']=max(merged[-1]['end'],r['end'])
        else:merged.append(r)
    if sum(r['end']-r['start'] for r in merged)>90:raise ValueError('AI edit exceeds 90 seconds; tighten the evidence')
    return merged

class Pipeline:
    def __init__(self,catalog,ai,runtime):
        self.catalog=catalog;self.ai=ai;self.runtime=Path(runtime)
        self.locks={};self.guard=threading.Lock()
    def lock(self,mid):
        with self.guard:return self.locks.setdefault(mid,threading.RLock())
    @staticmethod
    def ffprobe(video):
        try:
            p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(video)],capture_output=True,text=True,timeout=45)
            if p.returncode:raise ValueError('FFprobe cannot read the movie')
            duration=float(json.loads(p.stdout)['format']['duration'])
            if not math.isfinite(duration) or duration<=0:raise ValueError('Invalid movie duration')
            # A truncated download may advertise full duration in its header.
            p=subprocess.run(['ffmpeg','-v','error','-ss',str(max(0,duration-5)),'-i',str(video),'-frames:v','1','-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,timeout=45)
            if p.returncode or len(p.stdout)<100:raise ValueError('Movie is incomplete; its final frames cannot be decoded')
            return duration
        except FileNotFoundError:raise ValueError('Install FFmpeg and FFprobe on PATH, then restart the server') from None
    def analyze(self,m,progress):
        video,script=self.catalog.inputs(m);digest=self.catalog.fingerprint(m)
        folder=self.runtime/'artifacts'/m['id']/digest;path=folder/'analysis.json'
        with self.lock(m['id']):
            stored=Logbook(self.catalog).load(m)
            if stored:
                progress('logbook','Loading the saved AI action logbook · no new model call…')
                analysis=Logbook(self.catalog).project(m,stored,digest)
                write_json(path,analysis)
                return analysis,folder
            # Input-keyed successful results may be reused, never the old prepared demo.
            if path.is_file():return json.loads(path.read_text(encoding='utf-8')),folder
            progress('source','Checking the movie, timed transcript and supplied character photos…')
            duration=self.ffprobe(video);cues=scoped_cues(script,m,reader=read_srt)
            seen_windows=windows(m,duration)
            if cues and cues[-1]['end']>duration+1:raise ValueError('Transcript extends beyond the movie; check source version/offset')
            cast=[dict(id=c['id'],name=c['name'],actor=c.get('actor',''),identity_hint=c.get('identity_hint','')) for c in m['characters']]
            castids={c['id'] for c in cast};events=[];coverage=[];context=[]
            nova=getattr(self.ai,'provider','')=='nova'
            groups=[]
            for a,b in seen_windows:
                groups.extend(chunks([c for c in cues if a<=c['start'] and c['end']<=b],limit=24000 if nova else 4200,max_span=1800 if nova else 270))
            for n,group in enumerate(groups):
                progress('analysis',f'AI is finding the essential dialogue · passage {n+1} of {len(groups)}')
                # A little look-ahead helps classify repeated dialogue in causal context.
                # It may inform interpretation, but selected clips must remain in this group.
                following=[c for c in cues[group[-1]['id']:group[-1]['id']+8] if any(a<=group[0]['start'] and c['end']<=b for a,b in seen_windows)]
                data=dict(watched_ranges=seen_windows,database_story=bool(m.get('media_id')),cast=[] if m.get('media_id') else cast,max_events=10 if nova else 4,previous_context=context[-3:],following_context=[timed_cue(c) for c in following],cues=[timed_cue(c) for c in group])
                new,spans=self.extract_reviewed(data,group,castids,n,progress)
                events.extend(new);coverage.extend(spans)
                context.extend(e['summary'][:180] for e in new)
            if not events and not nova:raise ValueError('AI found no recap events that could be verified against the selected dialogue. No clips were invented. Try again or supply a more descriptive timed transcript.')
            relationships=[]
            if getattr(self.ai,'provider','')=='nova':
                timeline,visual_events,relationships=analyze_scenes(self,m,video,folder,duration,cues,events,progress)
                events.extend(visual_events)
                recovered,extra_links=([],[]) if m.get("media_id") else recover_cast(self,m,video,folder,timeline,events,cues,progress)
                events.extend(recovered);relationships.extend(extra_links)
            else:
                coverage=self.complete_dialogue_coverage(coverage,cues,events,progress)
                base=self.timeline(coverage,duration)
                base=[dict(x) for x in clamp_ranges(base,seen_windows)]
                timeline=self.assess_gaps(base,cues,progress)
            events=scope_events(events,windows(m))
            timeline=scope_timeline(timeline,windows(m),duration)
            if not events:raise ValueError('No scene or dialogue event could be supported in the watched portions. No recap was invented.')
            # Supplied cast stays visible, even when identity is unresolved in dialogue.
            facts=[dict(id=e['id'],people=e['characters'],fact=e['summary'][:180]) for e in events]
            # Bound synthesis input below the free tier token window; sample chronological evidence evenly.
            if len(facts)>60:facts=[facts[round(i*(len(facts)-1)/59)] for i in range(60)]
            if m.get('media_id'):
                tag_events(m,events)
                relationships=[]  # The viewer-aware DB map is served separately; never synthesize it here.
                characters=[]
            else:
                profiles=self.ai.json('profiles','Describe each supplied character using ONLY the chronological evidence. Return EVERY supplied cast ID. Use the supplied identity_hint as labeled metadata where dialogue is insufficient; explicitly state uncertainty. One concise description and motivation per cast ID. Mention uncertainty when unresolved. Do not invent aliases. Whole-film spoilers are allowed. Never omit a supplied cast ID. Do not invent unsupported relationships or motivations.',
                                      dict(cast=cast,evidence=facts),schemas.PROFILES,progress,max_tokens=1900)
                descriptions={}
                for c in profiles['characters']:
                    if c['id'] not in castids:raise ValueError('Unknown AI character ID')
                    descriptions[c['id']]=c
                characters=[]
                for c in m['characters']:
                    if c['id'] not in descriptions:
                        descriptions[c['id']]=dict(description=c.get('identity_hint') or 'Supplied cast member; role not established in the available evidence.',motivation='Not established in the available evidence.')
                    characters.append(dict(c,**{k:descriptions[c['id']][k] for k in ('description','motivation')},
                                           photo=('/inputs/'+m['id']+'/'+c['photo']) if c.get('photo') else None))
            coverage_stats={key:round(sum(t['end']-t['start'] for t in timeline if t.get('basis')==key),2) for key in ('dialogue','sampled_visual','inferred_context','unassessed')}
            analysis=dict(version=10,watched_ranges=seen_windows,watched_seconds=round(sum(b-a for a,b in seen_windows),3),source_offset_seconds=float(m.get('original_start_seconds',0)),relationships=relationships,fingerprint=digest,id=m['id'],title=m['title'],duration=duration,
                characters=characters,events=events,timeline=timeline,coverage_seconds=coverage_stats,
                source='/inputs/'+m['id']+'/'+m['video'],analysis_origin=getattr(self.ai,'label','AI')+' timed transcript and scene analysis',model=self.ai.model,provider=getattr(self.ai,'label','AI'))
            write_json(path,analysis)
            return analysis,folder
    def extract_reviewed(self,data,group,castids,chunk_index,progress):
        instruction=(
            'Extract up to {max_events} high-information events ONLY from cues, never from following_context. '
            'Use following_context and previous_context to interpret causality and changing relationships. '
            'Track who speaks, who owns an object/body part and who fears whom. Do not swap subject/object '
            'or infer the speaker merely from a name in dialogue. Honor supplied cast identity hints. '
            'Use supplied character IDs only; omit uncertain identities. '
            'For each event choose 1–4 ordered cue-ID ranges, ideally 15–60 seconds total. Use supplied start/end timecodes to measure duration. '
            'IMPORTANT: ranges.first and ranges.last are the integer id fields of cues, NEVER source_cue_id, seconds or timecodes. '
            'For an episode, start/end and start_timecode/end_timecode are episode-relative; original_start_timecode/original_end_timecode, when present, identify the original full-film positions. '
            'An event range must include the cue whose text supports its title and summary. Evidence is exact dialogue, not a timestamp. '
            'Keep the cause, setup and outcome together so the clip is understandable. Do not make an isolated 1–3 second progress announcement its own event: combine it with the surrounding causal conversation. '
            'Evidence must copy a short exact passage from selected cues, preferably one cue. '
            'Do not add enclosing quotes or paraphrase the evidence. '
            'Critical means a cause, goal, revelation or relationship change needed to understand the plot; '
            'secondary adds context; filler adds little story information. A breakup, apology, technical '
            'operation or repeated dialogue in a rehearsal/simulation can be plot-critical. Do not label '
            'them filler solely because they sound routine, emotional or repetitive. '
            'Relationships need explicit evidence; location must be stated or "Not stated". '
            'Classify supported dialogue into ordered coverage ranges with category and reason. Gaps are allowed and remain unclassified; missing labels must never be guessed or called filler merely because they were not selected as events. '
            'Do not classify unseen visual gaps. If repair_feedback is supplied, redo the analysis to '
            'resolve it using the transcript; do not invent an answer to satisfy the reviewer.')
        max_events=min(12,max(1,int(data.get('max_events',4))))
        instruction=instruction.format(max_events=max_events)
        if data.get('watched_ranges'):instruction+=' Only supplied story ranges are available. Include supported earlier scenes even if the viewer skipped them. Gaps without evidence are unknown; never infer missing events or reveal scenes after the playback cutoff.'
        schema=schemas.ANALYSIS
        if data.get('database_story'):
            instruction+=' Character identities and graph connections are managed by the supplied database. Return empty characters and relationships arrays. Do not generate character profiles or graph links; analyze story events and relevance only.'
            schema=copy.deepcopy(schema)
            for key in ('characters','relationships'):schema['properties']['events']['items']['properties'][key]['maxItems']=0
        issues=[]; retained=None
        for attempt in range(2):
            payload=dict(data)
            if issues:payload['repair_feedback']=issues
            answer=self.ai.json('extract',instruction,payload,schema,progress,max_tokens=6000 if max_events>4 else 2900)
            try:
                events,spans,review_coverage,structural_issues=self.prepare_chunk(answer,group,castids,chunk_index,max_events)
                if not events:
                    if retained is None:retained=([],spans)
                    issues=structural_issues or ['No structurally valid dialogue event; select shorter supported cue ranges.']
                    self.ai.log(dict(task='validation',status='empty_dialogue_pass',issues=issues))
                    if hasattr(self.ai,'invalidate'):self.ai.invalidate('extract')
                    continue
                progress('review','AI is checking each clip and relationship against its selected dialogue…')
                # Supply canonical text and cue IDs, not the extractor's short quotation.
                proposed=[dict(index=j,**{k:e[k] for k in ('title','summary','category','why','characters','location','relationships')},
                               selected_cues=[c for c in group if c['id'] in e['evidence_cue_ids']])
                          for j,e in enumerate(events)]
                review=self.ai.json('review',
                    'Audit each event independently. Return exactly one verdict per event index. '
                    'An event is supported only if its title, summary, category justification and character '
                    'identities follow from its selected_cues. Context may explain significance but may '
                    'not substitute for evidence inside the selected clip. Sparse subtitles cannot prove '
                    'visual action, technical operations, speaker identity or ownership by themselves. '
                    'A name in dialogue is not necessarily the speaker. Synchronizing or locking memory does not mean overwriting it. Stage or camera directions do not establish a spacecraft launch. Do not turn an ambiguous "Cut!" '
                    'into an operation-abort decision. Do not invent facts to complete the story. '
                    'Judge relationships separately: list zero-based unsupported_relationships indexes; '
                    'a bad relationship alone does NOT make an otherwise supported event fail. '
                    'Use supported=false with a concise cue-specific reason for unsupported event claims. '
                    'Do not fail supported events because other events or desired plot details are missing. '
                    'Also list zero-based unsupported_coverage indexes for unjustified timeline labels; '
                    'uncertain visual gaps must not be labelled filler. Empty events is valid.',
                    dict(cast=data['cast'],previous_context=data.get('previous_context',[]),
                         following_context=data.get('following_context',[]),events=proposed,
                         cues=group,coverage=[dict(index=i,**row) for i,row in enumerate(review_coverage)]),schemas.review_for(events,review_coverage),progress,max_tokens=2600 if max_events>4 else 1500)
                accepted,checked_spans,issues=self.apply_review(review,events,spans,review_coverage,group)
                issues=structural_issues+issues
                # Prefer the latest reviewed result on a tie, never unreviewed output.
                if retained is None or len(accepted)>=len(retained[0]):retained=(accepted,checked_spans)
                if not issues:return accepted,checked_spans
            except (ValueError,KeyError,TypeError) as exc:
                issues=['Transcript/review validation: '+str(exc)]
            self.ai.log(dict(task='validation',status='repair_requested' if attempt==0 else 'claims_omitted',
                             passage=chunk_index+1,issues=issues))
            if hasattr(self.ai,'invalidate'):self.ai.invalidate('extract')
            if attempt==0:progress('repair','AI is correcting disputed clips; verified evidence is being kept…')
        if retained is not None:
            progress('review','Unsupported claims omitted · continuing with verified dialogue.')
            return retained
        # Malformed responses are not usable, and are never replaced by invented content.
        raise ValueError('AI analysis returned invalid evidence or review data after correction: '+'; '.join(issues)[:500])

    def apply_review(self,review,events,spans,coverage,group):
        """Keep independently supported events; never promote a literal quote to a plot fact."""
        verdicts=review['events'];bad_coverage=review['unsupported_coverage']
        if not isinstance(verdicts,list):raise ValueError('Invalid event review')
        seen=set();accepted=[];issues=[]
        for v in verdicts:
            j=v['index']
            if type(j) is not int or j<0 or j>=len(events) or j in seen:raise ValueError('Invalid event review index')
            seen.add(j);e=events[j];bad=v['unsupported_relationships']
            if not isinstance(v['supported'],bool) or not isinstance(v['reason'],str):raise ValueError('Invalid review verdict')
            if not isinstance(bad,list) or any(type(i) is not int or not 0<=i<len(e['relationships']) for i in bad):raise ValueError('Invalid relationship review index')
            if bad:
                self.ai.log(dict(task='validation',status='relationships_omitted',event_id=e['id'],indexes=bad,reason=v['reason']))
            if v['supported']:
                accepted.append(dict(e,relationships=[r for i,r in enumerate(e['relationships']) if i not in bad]))
            else:issues.append('Event %s: %s' % (j,v['reason']))
        for j in set(range(len(events)))-seen:issues.append('Event %s: no supporting review verdict was returned' % j)
        if not isinstance(bad_coverage,list) or any(type(i) is not int or not 0<=i<len(coverage) for i in bad_coverage):raise ValueError('Invalid coverage review index')
        # Hide classifications covering rejected facts, even if the reviewer missed the label.
        byid={c['id']:c for c in group}
        bad_intervals=[(byid[c['first']]['start'],byid[c['last']]['end']) for i,c in enumerate(coverage) if i in bad_coverage]
        for j,e in enumerate(events):
            if not next((v['supported'] for v in verdicts if v['index']==j),False):
                bad_intervals.extend((r['start'],r['end']) for r in e['ranges'])
        checked=[]
        for span in spans:
            boundaries=sorted({span['start'],span['end']}|{max(span['start'],min(span['end'],x)) for interval in bad_intervals for x in interval})
            for a,b in zip(boundaries,boundaries[1:]):
                if b<=a:continue
                bad=any(x<b and y>a for x,y in bad_intervals)
                checked.append(dict(span,start=a,end=b,**(dict(category='unclassified',basis='unassessed',
                    why='Insufficient transcript evidence to validate this classification.') if bad else {})))
        if bad_coverage:self.ai.log(dict(task='validation',status='coverage_unclassified',indexes=bad_coverage))
        accepted.sort(key=lambda e:e['start'])
        return accepted,checked,issues

    @staticmethod
    def validate_event(e,group,castids,chunk_index,j):
        byid={c['id']:c for c in group}
        if e['category'] not in CATEGORIES-{'unclassified'}:raise ValueError('Invalid AI category')
        if not e['title'] or not e['summary'] or not e['why']:raise ValueError('Missing AI explanation')
        if not set(e['characters'])<=castids:raise ValueError('Unknown character in AI analysis')
        ranges=ranges_to_edl(e['ranges'],group)
        evidence=' '.join(byid[i]['text'] for r in ranges for i in range(r['first'],r['last']+1))
        if not quote_is_supported(e['evidence'],evidence):raise ValueError('AI quote contains words absent from the selected transcript')
        for rel in e['relationships']:
            if rel['source']==rel['target'] or not {rel['source'],rel['target']}<=set(e['characters']):raise ValueError('Relationship outside supported event cast')
        selected_cues=[byid[i] for r in ranges for i in range(r['first'],r['last']+1)]
        return dict(e,id=f'e{chunk_index:03d}_{j:02d}',evidence=evidence,anchor_cue_ids=quote_cue_ids(e['evidence'],selected_cues),
                           evidence_cue_ids=[i for r in ranges for i in range(r['first'],r['last']+1)],
                           ranges=ranges,start=ranges[0]['start'],end=ranges[-1]['end'])

    @staticmethod
    def validate_chunk(answer,group,castids,chunk_index):
        byid={c['id']:c for c in group}
        if len(answer['events'])>4:raise ValueError('Too many extracted events')
        events=[Pipeline.validate_event(e,group,castids,chunk_index,j) for j,e in enumerate(answer['events'])]
        expected=group[0]['id'];spans=[]
        for s in answer['coverage']:
            if s['first']!=expected or s['last']<s['first'] or s['last'] not in byid or s['category'] not in CATEGORIES-{'unclassified'}:
                raise ValueError('AI timeline does not partition the supplied cues')
            previous=None
            for i in range(s['first'],s['last']+1):
                c=byid[i]
                if previous and 0<c['start']-previous['end']<=12:
                    spans.append(dict(start=previous['end'],end=c['start'],category=s['category'],basis='inferred_context',
                                      why='Context between adjacent classified dialogue cues: '+s['why']))
                spans.append(dict(start=c['start'],end=c['end'],category=s['category'],basis='dialogue',why=s['why']))
                previous=c
            expected=s['last']+1
        if expected!=group[-1]['id']+1:raise ValueError('AI coverage omitted transcript cues')
        return events,spans
    def prepare_chunk(self,answer,group,castids,chunk_index,max_events=4):
        """Partial coverage is an annotation gap, never a reason to lose verified events."""
        events=[];issues=[]
        if len(answer['events'])>max_events:issues.append('Too many events; return at most '+str(max_events))
        for j,source in enumerate(answer['events'][:max_events]):
            e=dict(source);valid=[]
            for rel in e['relationships']:
                if rel['source']!=rel['target'] and {rel['source'],rel['target']}<=set(e['characters']):valid.append(rel)
                else:self.ai.log(dict(task='validation',status='relationship_structure_omitted',passage=chunk_index+1,event_index=j))
            e['relationships']=valid
            try:events.append(self.validate_event(e,group,castids,chunk_index,j))
            except (ValueError,KeyError,TypeError) as exc:issues.append('Event %s: %s' % (j,exc))
        coverage,spans=self.sparse_coverage(answer['coverage'],group)
        return events,spans,coverage,issues

    def sparse_coverage(self,rows,group):
        byid={c['id']:c for c in group};assigned={};conflicts=set()
        for index,row in enumerate(rows):
            try:
                a,b=row['first'],row['last'];category=row['category'];why=row['why']
                if type(a) is not int or type(b) is not int or a>b or any(i not in byid for i in range(a,b+1)):
                    raise ValueError('Coverage range is outside supplied cues')
                if category not in CATEGORIES or not isinstance(why,str) or not why.strip():raise ValueError('Invalid coverage label')
                for cue in range(a,b+1):
                    if cue in assigned and assigned[cue]['category']!=category:conflicts.add(cue)
                    else:assigned.setdefault(cue,dict(category=category,why=why))
            except (ValueError,KeyError,TypeError) as exc:
                self.ai.log(dict(task='coverage',status='row_omitted',index=index,error=str(exc)))
        for cue in conflicts:assigned.pop(cue,None)
        missing=[i for i in byid if i not in assigned]
        if missing:self.ai.log(dict(task='coverage',status='incomplete',unclassified_cue_ids=missing,conflicting_cue_ids=sorted(conflicts)))
        # Rebuild only genuinely supplied classifications into ordered, nonoverlapping rows.
        clean=[]
        for c in group:
            label=assigned.get(c['id'])
            if label:
                if clean and clean[-1]['last']==c['id']-1 and all(clean[-1][k]==label[k] for k in ('category','why')):clean[-1]['last']=c['id']
                else:clean.append(dict(first=c['id'],last=c['id'],**label))
        spans=[];previous=None
        for c in group:
            label=assigned.get(c['id'])
            if label is None:label=dict(category='unclassified',why='AI did not provide a verified classification for this subtitle.')
            basis='dialogue' if label['category']!='unclassified' else 'unassessed'
            if previous and basis=='dialogue' and previous[1]==label and 0<c['start']-previous[0]['end']<=12:
                spans.append(dict(start=previous[0]['end'],end=c['start'],**label,basis='inferred_context'))
            spans.append(dict(start=c['start'],end=c['end'],**label,basis=basis,cue_id=c['id']))
            previous=(c,label)
        return clean,spans

    def complete_dialogue_coverage(self,spans,cues,events,progress):
        """An independent, optional AI pass fills missing dialogue labels, not events."""
        pending=[(i,s) for i,s in enumerate(spans) if s['category']=='unclassified' and s.get('cue_id')]
        byid={c['id']:c for c in cues}
        for offset in range(0,len(pending),36):
            batch=pending[offset:offset+36];ids=sorted({s['cue_id'] for _,s in batch})
            context_ids=sorted({j for cue in ids for j in (cue-1,cue,cue+1) if j in byid})
            progress('coverage','AI is classifying remaining subtitles separately from recap extraction…')
            try:
                answer=self.ai.json('coverage_repair',
                    'Classify only the requested cue_ids by plot relevance using real dialogue and neighboring context. '
                    'Return each requested ID once. Use critical for a needed cause/goal/revelation/relationship change, '
                    'secondary for useful context, filler only when it adds little information. Unselected dialogue is '
                    'not automatically filler. Use unclassified when unsure. Preserve speaker/ownership uncertainty; '
                    'do not invent visual actions. Quote exact evidence from that same cue for every classified item.',
                    dict(cue_ids=ids,cues=[timed_cue(byid[i]) for i in context_ids],verified_context=[e['summary'][:180] for e in events][-10:]),
                    schemas.coverage_for_cues(ids),progress,max_tokens=3000)
                found={};allowed=set(ids)
                for item in answer['classifications']:
                    cue=item['cue_id']
                    if type(cue) is not int or cue not in allowed or cue in found:raise ValueError('Invalid or duplicate coverage cue ID')
                    if item['category'] not in CATEGORIES:raise ValueError('Invalid coverage category')
                    if item['category']!='unclassified' and (not item['why'] or not quote_is_supported(item['evidence'],byid[cue]['text'])):
                        raise ValueError('Coverage classification lacks exact source evidence')
                    found[cue]=item
                for i,original in batch:
                    item=found.get(original['cue_id'])
                    if item and item['category']!='unclassified':
                        spans[i]=dict(original,category=item['category'],why=item['why'],basis='dialogue',classification_origin='supplementary_dialogue_pass')
                if set(found)!=allowed:self.ai.log(dict(task='coverage',status='still_unclassified',cue_ids=sorted(allowed-set(found))))
            except (AIUnavailable,ValueError,KeyError,TypeError) as exc:
                self.ai.log(dict(task='coverage',status='unassessed',error=str(exc)))
                if hasattr(self.ai,'invalidate'):self.ai.invalidate('coverage_repair')
        return spans

    @staticmethod
    def timeline(spans,duration):
        result=[];cursor=0
        for s in sorted(spans,key=lambda s:s['start']):
            start=max(cursor,s['start']);end=min(duration,s['end'])
            if start>cursor:result.append(dict(start=cursor,end=start,category='unclassified',basis='unassessed',gap=True,why='No dialogue evidence; visual importance has not been assessed.'))
            if end>start:result.append(dict(s,start=start,end=end));cursor=end
        if cursor<duration:result.append(dict(start=cursor,end=duration,category='unclassified',basis='unassessed',gap=True,why='No transcript evidence; not labelled filler.'))
        return result
    def assess_gaps(self,timeline,cues,progress):
        # Text-only context inference is visibly distinct from dialogue-backed labels.
        # Never infer opening/end sections beyond the timed transcript's coverage.
        targets=[(i,t) for i,t in enumerate(timeline) if t.get('gap') and 2<t['end']-t['start']<=90
                 and t['start']>=cues[0]['end'] and t['end']<=cues[-1]['start']]
        for offset in range(0,len(targets),10):
            batch=targets[offset:offset+10];intervals=[]
            for i,t in batch:
                before=[c for c in cues if c['end']<=t['start']+.001][-3:]
                after=[c for c in cues if c['start']>=t['end']-.001][:3]
                intervals.append(dict(index=i,start=t['start'],end=t['end'],before=[timed_cue(c) for c in before],after=[timed_cue(c) for c in after]))
            progress('coverage','AI is assessing dialogue gaps · inferred context is shown with hatching…')
            try:
                answer=self.ai.json('timeline_context',
                    'Estimate the plot RELEVANCE of each silent interval from its bracketing dialogue only. '
                    'You have no frames and cannot know visual actions, identities, scene changes or credits. '
                    'Classify critical/secondary/filler only when BOTH sides establish a coherent causal topic. '
                    'Otherwise choose unclassified. This is explicitly uncertain context inference, never visual analysis. '
                    'Return each supplied index exactly once, a brief uncertainty-aware explanation and evidence_cue_ids '
                    'from before AND after. Do not classify based solely on a command or sound. Do not invent events.',
                    dict(intervals=intervals),schemas.GAPS,progress,max_tokens=1700)
                allowed={x['index']:x for x in intervals};seen=set()
                for row in answer['assessments']:
                    i=row['index'];category=row['category'];ids=row['evidence_cue_ids']
                    if type(i) is not int or i not in allowed or i in seen:raise ValueError('Invalid gap review index')
                    seen.add(i);item=allowed[i]
                    before={c['id'] for c in item['before']};after={c['id'] for c in item['after']}
                    if category not in CATEGORIES or not isinstance(ids,list) or any(type(v) is not int for v in ids) or not set(ids)<=before|after:
                        raise ValueError('Unsupported gap classification')
                    if category!='unclassified':
                        if not set(ids)&before or not set(ids)&after or not row['why']:raise ValueError('Gap inference needs bracketing evidence')
                        timeline[i]=dict(timeline[i],category=category,basis='inferred_context',why=row['why'],evidence_cue_ids=ids)
                if seen!=set(allowed):raise ValueError('Gap review omitted intervals')
            except (AIUnavailable,ValueError,KeyError,TypeError) as exc:
                # Gap inference is supplementary. Verified dialogue remains usable, not a fallback.
                # Reject the complete malformed batch, including previously parsed rows.
                for i,original in batch:timeline[i]=original
                self.ai.log(dict(task='timeline_context',status='unassessed',error=str(exc)))
                if hasattr(self.ai,'invalidate'):self.ai.invalidate('timeline_context')
        return timeline

    @staticmethod
    def validate_queue(answer,allowed,cues,budget):
        planned=[];issues=[];seen=set();seconds=0
        if len(answer['items'])>5:issues.append('Choose at most 5 distinct events.')
        for index,item in enumerate(answer['items'][:5]):
            try:
                eid=item['event_id']
                if eid not in allowed:raise ValueError('Unknown event_id %r; copy an allowed ID EXACTLY, including all zeros.' % eid)
                if eid in seen:raise ValueError('Duplicate event_id '+eid)
                event=allowed[eid]
                ranges=ranges_to_edl(item['ranges'],cues,event['context_ranges'])
                bounds=[[r['start'],r['end']] for r in event['context_ranges']]
                ranges=clamp_ranges(ranges,bounds)
                anchors=event['anchor_cue_ids']
                if not all(any(r['first']<=c<=r['last'] for r in ranges) for c in anchors):raise ValueError('Cut omitted the event’s anchor dialogue')
                length=sum(r['end']-r['start'] for r in ranges)
                if length+.05<event['min_clip_seconds']:raise ValueError('Clip is only %.1fs; keep at least %.1fs of setup and outcome' % (length,event['min_clip_seconds']))
                if not 7-.001<=length<=45+.001:raise ValueError('A recap clip must be 7-45 seconds total')
                if seconds+length>budget+.5:raise ValueError('Queue exceeds budget; shorten or select fewer contextual clips')
                for prev in planned:
                    overlap=sum(max(0,min(a['end'],b['end'])-max(a['start'],b['start'])) for a in ranges for b in prev['ranges'])
                    if overlap>.8*min(length,prev['duration']):raise ValueError('This clip repeats nearly the same footage as '+prev['event_id'])
                if not isinstance(item['reason'],str) or not item['reason'].strip():raise ValueError('Missing explanation for the selected cut')
                seen.add(eid);seconds+=length;planned.append(dict(event_id=eid,reason=item['reason'],ranges=ranges,duration=round(length,2)))
            except (ValueError,KeyError,TypeError) as exc:issues.append('Item %s: %s' % (index,exc))
        return planned,issues

    def expand_context(self,m,analysis,folder,state,progress):
        """If a selected character runs out of footage, search new source intervals."""
        if m.get('media_id') or getattr(self.ai,'provider','')!='nova':return analysis
        rated=next((e for e in analysis['events'] if e['id']==state['last_rated']),None)
        targets=set(state.get('pair') or ([state['character']] if state.get('character') else (rated['characters'] if rated else [])))
        if not targets:return analysis
        excluded={q['event_id'] for q in state.get('clip_history',[])}
        def matches(e):
            if state.get('pair'):return any({r['source'],r['target']}==targets for r in e['relationships'])
            return bool(targets&set(e['characters']))
        available=[e for e in analysis['events'] if matches(e) and e['id'] not in excluded and state['memory'].get(e['id'])!=1 and e['id']!=state['last_rated']]
        if len(available)>=2:return analysis
        with self.lock(m['id']):
            latest=json.loads((folder/'analysis.json').read_text(encoding='utf-8'))
            video,script=self.catalog.inputs(m);cues=scoped_cues(script,m,reader=read_srt)
            used=[r for q in state.get('clip_history',[]) for r in q['ranges']]
            used.extend(r for e in latest['events'] if e.get('visual_scene') and matches(e) for r in e['ranges'])
            progress('context','AI is searching previously unused footage for more character context…')
            fractions=(.1,.3,.5,.7,.9) if state.get('context_depth',0)>0 else (.03,.97)
            new,links=recover_cast(self,m,video,folder,latest['timeline'],latest['events'],cues,progress,
                targets=targets,exclude_ranges=used,fractions=fractions)
            ids={e['id'] for e in latest['events']};new=[e for e in new if e['id'] not in ids]
            if new:
                latest['events'].extend(new);latest['relationships'].extend(links);write_json(folder/'analysis.json',latest)
            return latest

    def recommend(self,m,analysis,folder,state,progress):
        byid={e['id']:e for e in analysis['events']}
        excluded=set(state.get('exclude_event_ids',[]))
        candidates=[e for e in byid.values() if state['memory'].get(e['id'])!=1 and e['id']!=state['last_rated'] and e['id'] not in excluded]
        if state['character']:candidates=[e for e in candidates if state['character'] in e['characters']]
        if state['pair']:candidates=[e for e in candidates if any(set((r['source'],r['target']))==set(state['pair']) for r in e['relationships'])]
        forgotten=[byid[k] for k,v in state['memory'].items() if v==0 and k in byid]
        threads={t for e in forgotten for t in e['threads']}
        def retrieval(e):return (len(set(e['threads'])&threads)*3+(2 if e['category']=='critical' else 0),-e['start'])
        candidates=sorted(candidates,key=retrieval,reverse=True)
        if analysis.get('persistent_logbook'):return recommend_stored(self,m,analysis,folder,state,candidates,progress)
        candidates=candidates[:18]
        if not candidates:return dict(items=[],explanation='All matching evidence is remembered, or no other supported scene matches this filter.',model=self.ai.model,provider=getattr(self.ai,'label','AI'))
        cues=scoped_cues(self.catalog.inputs(m)[1],m,reader=read_srt)
        if any(e.get('visual_scene') for e in candidates):
            return self.recommend_seconds(m,analysis,folder,state,candidates,cues,progress)
        candidate_data=[]
        for e in candidates:
            local=context_cues(e,cues,windows(m))
            windows_for_event=clamp_ranges(context_window(e,local),windows(m))
            selected=[timed_cue(c) for c in cues if any(r['first']<=c['id']<=r['last'] for r in windows_for_event)]
            maximum=sum(r['end']-r['start'] for r in windows_for_event)
            if maximum<8:
                self.ai.log(dict(task='recommend_validation',status='isolated_cue_omitted',event_id=e['id'],available_seconds=round(maximum,2)))
                continue
            candidate_data.append({**{k:e[k] for k in ('id','title','summary','category','characters','threads','ranges')},
                'anchor_cue_ids':e.get('anchor_cue_ids') or [e['ranges'][0]['first']],
                'context_ranges':windows_for_event,'min_clip_seconds':round(min(12,maximum),3),'cues':selected})
        if not candidate_data:return dict(items=[],explanation='Only isolated subtitle lines match this filter; there is not enough nearby dialogue for an understandable recap clip.',model=self.ai.model,provider=getattr(self.ai,'label','AI'))
        while len(json.dumps(candidate_data,ensure_ascii=False))>12000 and len(candidate_data)>2:candidate_data.pop()
        allowed={e['id']:e for e in candidate_data};schema=schemas.queue_for_candidates(allowed)
        data=dict(budget_seconds=state['budget'],memory=state['memory'],last_rated=state['last_rated'],interest=state['interest'],
                  character=state['character'],pair=state['pair'],missing_context=[e['summary'][:180] for e in forgotten[-5:]],candidates=candidate_data)
        instruction=(
            'Build an ordered recap queue of up to 5 DISTINCT events within budget_seconds. Copy event_id EXACTLY '
            'from candidates.id, including all zeros. Never return a remembered or last-rated ID. '
            'For Help me remember prefer different causes, earlier setup or related revelations that explain the missing context. '
            'Honor selected character/relationship. Use the transcript start/end seconds and timecodes to plan cuts. '
            'Choose 1–4 ordered cue-ID ranges inside context_ranges (which include nearby setup/outcome beyond the narrow evidence). '
            'Include every anchor_cue_id and at least min_clip_seconds per clip; be 7–45s TOTAL, ideally 7–30s of essential action and context. '
            'A one-line 1–3s progress/abort announcement is not an understandable recap. Keep the surrounding conversation. '
            'Avoid selecting overlapping footage for multiple events: combine the understanding into one useful clip. '
            'Trim incidental lines only when setup, causality and payoff survive. Total budget is a ceiling, not a target to fill. '
            'Give specific evidence-grounded reasons, without adding facts absent from candidate dialogue. '
            'If repair_feedback is supplied, regenerate the entire queue correcting those exact errors. '
            'Empty items is allowed when no candidate can provide a useful contextual clip.')
        best=None;issues=[]
        for attempt in range(3):
            progress('recommendation','AI is choosing contextual clips and redefining your queue…' if not attempt else 'AI is repairing clip choices · keeping verified edits…')
            payload=dict(data)
            if issues:payload['repair_feedback']=issues
            try:answer=self.ai.json('recommend',instruction,payload,schema,progress,max_tokens=1600)
            except AIResponseInvalid:
                if best:break
                raise
            plan,issues=self.validate_queue(answer,allowed,cues,state['budget'])
            if plan and (best is None or len(plan)>=len(best[0])):best=(plan,answer['explanation'])
            if not issues:
                best=(plan,answer['explanation']);break
            self.ai.log(dict(task='recommend_validation',status='repair_requested' if attempt<2 else 'items_omitted',issues=issues))
            if hasattr(self.ai,'invalidate'):self.ai.invalidate('recommend')
        if best is None:raise ValueError('AI could not select a valid contextual recap after correction. '+ '; '.join(issues)[:350])
        plan,explanation=best;queue=[]
        for item in plan:
            progress('editing','Cutting a complete context · '+byid[item['event_id']]['title']+'…')
            if not all(covers(windows(m),r['start'],r['end']) for r in item['ranges']):raise ValueError('Clip escaped the watched portions')
            clip=self.render(self.catalog.inputs(m)[0],folder,item['ranges'],progress)
            queue.append(dict(item,clip=clip))
        if issues:explanation+=' Some proposed edits were omitted because they failed validation.'
        return dict(items=queue,explanation=explanation,model=self.ai.model,provider=getattr(self.ai,'label','AI'),total_seconds=round(sum(x['duration'] for x in queue),2))
    def recommend_seconds(self,m,analysis,folder,state,candidates,cues,progress):
        """One planner for dialogue edits AND genuine silent visual scenes."""
        allowed={};history=state.get('clip_history',[]);payload=[]
        for e in candidates:
            local=context_cues(e,cues,windows(m))
            candidate_windows=e['ranges'] if e.get('visual_scene') else context_window(e,local,target_seconds=28)
            candidate_windows=clamp_ranges(candidate_windows,windows(m))
            maximum=sum(r['end']-r['start'] for r in candidate_windows)
            if maximum<7:continue
            anchors=[dict(start=c['start'],end=c['end']) for c in cues if c['id'] in e.get('anchor_cue_ids',[])]
            data=dict(id=e['id'],title=e['title'],summary=e['summary'],characters=e['characters'],category=e['category'],
                allowed_ranges=[dict(start=r['start'],end=r['end']) for r in candidate_windows],
                anchor_dialogue=anchors,frame_times=e.get('frame_times',[]),visual_evidence=e.get('evidence','') if e.get('visual_scene') else '',
                cues=[timed_cue(c) for c in cues if any(c['start']<r['end'] and c['end']>r['start'] for r in candidate_windows)],min_seconds=min(7,maximum))
            allowed[e['id']]=data;payload.append(data)
        if not allowed:return dict(items=[],explanation='No new verified scene is available for this selection.',model=self.ai.model,provider=getattr(self.ai,'label','AI'))
        data=dict(candidates=payload,budget_seconds=state['budget'],character=state['character'],pair=state['pair'],
            missing_context=[e['summary'] for e in analysis['events'] if state['memory'].get(e['id'])==0][-5:],
            context_depth=state.get('context_depth',0),previous_source_ranges=[r for item in history for r in item['ranges']])
        instruction=('Select up to 3 NEW useful contextual clips. Honor the character or pair filter strictly. '
            'For a missing memory, prefer a DIFFERENT scene giving causes, setup or the outcome; deeper context_depth means more background. '
            'Use only candidate IDs and source start/end seconds inside allowed_ranges. Each clip must contain every anchor_dialogue '
            'in full, be at least min_seconds, and be 7–45 seconds TOTAL, ideally 7–30 seconds including action setup and outcome. '
            'A visual scene must include at least one of its supplied frame_times; those frames support the identity claim. '
            'Choose 1–4 ordered source ranges to remove incidental material only if context remains clear. '
            'Do not repeat nearly the same footage across clips or previous_source_ranges. '
            'Keep the sum within budget_seconds. Return empty items if all remaining scenes repeat previously shown footage '
            'or no supplied evidence helps. Explain each clip using only supplied evidence. '
            'If repair_feedback is present, regenerate the entire plan correcting those errors.')
        best=None;issues=[]
        for attempt in range(3):
            progress('recommendation','AI is finding new context and editing scene-length clips…')
            try:
                answer=self.ai.json('recommend_scenes',instruction,dict(data,**({'repair_feedback':issues} if issues else {})),
                    schemas.time_queue_for_candidates(allowed),progress,max_tokens=1800)
            except AIResponseInvalid:
                if best:break
                raise
            plan=[];issues=[];seen=set();seconds=0
            for item in answer['items'][:3]:
                try:
                    eid=item['event_id']
                    if eid not in allowed or eid in seen:raise ValueError('Unavailable or duplicate event ID')
                    candidate=allowed[eid];ranges=item['ranges']
                    if not 1<=len(ranges)<=4:raise ValueError('Use 1–4 source ranges')
                    previous=-1
                    for r in ranges:
                        a,b=r['start'],r['end']
                        if not all(type(x) in (float,int) and math.isfinite(x) for x in (a,b)) or a<previous or b<=a:raise ValueError('Invalid source time/order')
                        if not covers(windows(m),a,b):raise ValueError('Cut escaped watched portions')
                        if not any(a>=w['start']-.001 and b<=w['end']+.001 for w in candidate['allowed_ranges']):raise ValueError('Cut escaped supported scene/context')
                        previous=b
                    length=sum(r['end']-r['start'] for r in ranges)
                    if length+.05<candidate['min_seconds'] or length>45:raise ValueError('Keep at least %.1fs of scene context' % candidate['min_seconds'])
                    if not all(any(r['start']<=a['start']+.05 and r['end']>=a['end']-.05 for r in ranges) for a in candidate['anchor_dialogue']):raise ValueError('Cut omitted required anchor dialogue')
                    if candidate['frame_times'] and not any(r['start']<=t<=r['end'] for t in candidate['frame_times'] for r in ranges):raise ValueError('Cut omitted the visual identity evidence')
                    for old in history+plan:
                        overlap=sum(max(0,min(a['end'],b['end'])-max(a['start'],b['start'])) for a in ranges for b in old['ranges'])
                        oldlength=sum(r['end']-r['start'] for r in old['ranges'])
                        if oldlength and overlap>.8*min(length,oldlength):raise ValueError('Nearly repeats previously queued footage; choose a different scene')
                    if seconds+length>state['budget']+.5:raise ValueError('New clips exceed budget')
                    if not item['reason'].strip():raise ValueError('Missing clip explanation')
                    seen.add(eid);seconds+=length;plan.append(dict(event_id=eid,reason=item['reason'],ranges=ranges,duration=round(length,2)))
                except (ValueError,KeyError,TypeError) as exc:issues.append(str(exc))
            if len(answer['items'])>3:issues.append('Select at most 3 new clips')
            if plan and (best is None or len(plan)>len(best[0])):best=(plan,answer['explanation'])
            if not issues:best=(plan,answer['explanation']);break
            self.ai.log(dict(task='recommend_scenes_validation',status='repair',issues=issues))
            if hasattr(self.ai,'invalidate'):self.ai.invalidate('recommend_scenes')
        if best is None:raise ValueError('No new contextual edit passed validation: '+'; '.join(issues))
        plan,explanation=best;items=[]
        for item in plan:
            progress('editing','Cutting new context · '+allowed[item['event_id']]['title'])
            if not all(covers(windows(m),r['start'],r['end']) for r in item['ranges']):raise ValueError('Clip escaped the watched portions')
            items.append(dict(item,clip=self.render(self.catalog.inputs(m)[0],folder,item['ranges'],progress)))
        return dict(items=items,explanation=explanation,model=self.ai.model,provider=getattr(self.ai,'label','AI'),total_seconds=round(sum(x['duration'] for x in items),2))

    def render(self,video,folder,ranges,progress=lambda *args:None):
        key=hashlib.sha256((str(video)+json.dumps(ranges,sort_keys=True)).encode()).hexdigest()[:24]
        output=folder/'clips'/f'{key}.mp4';output.parent.mkdir(parents=True,exist_ok=True)
        with self.lock('render_'+key):
            if not output.is_file():
                temp=output.with_suffix('.partial.mp4');parts=[]
                try:
                    # Each seek decodes only its selected source range. Re-encode for accurate boundaries.
                    for n,r in enumerate(ranges):
                        part=output.parent/f'{key}-{n}.part.mp4';parts.append(part)
                        command=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-ss',str(r['start']),'-i',str(video),'-t',str(r['end']-r['start']),
                                 '-map','0:v:0','-map','0:a:0?','-vf','scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1',
                                 '-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-r','24','-c:a','aac','-ar','48000','-ac','2','-movflags','+faststart',str(part)]
                        run=subprocess.run(command,capture_output=True,text=True,timeout=180)
                        if run.returncode:raise ValueError('FFmpeg cut failed: '+run.stderr[-350:])
                    listing=output.parent/f'{key}.concat.txt'
                    listing.write_text(''.join("file '"+p.name+"'\n" for p in parts),encoding='utf-8')
                    run=subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','concat','-safe','1','-i',str(listing),'-c','copy','-movflags','+faststart',str(temp)],capture_output=True,text=True,timeout=60)
                    if run.returncode:raise ValueError('FFmpeg montage failed: '+run.stderr[-350:])
                    temp.replace(output)
                finally:
                    for p in parts:p.unlink(missing_ok=True)
                    temp.unlink(missing_ok=True)
                    (output.parent/f'{key}.concat.txt').unlink(missing_ok=True)
        return '/generated/'+output.relative_to(self.runtime/'artifacts').as_posix()
