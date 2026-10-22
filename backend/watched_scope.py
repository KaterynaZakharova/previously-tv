"""Bounds shared by transcript analysis, frame sampling, clip planning and playback.

Times stay episode-local; story prefixes include skipped scenes before the playhead.
The watched_ranges field is retained for compatibility with stored analyses.
"""
import copy
from .catalog import read_srt

MIN_RECAP_SECONDS=12
EPS=1e-6


def windows(manifest,duration=None):
    raw=manifest.get('watched_ranges')
    if raw is None:return [[0,float(duration)]] if duration is not None else None
    return [[float(a),min(float(b),float(duration)) if duration is not None else float(b)] for a,b in raw if b>a and (duration is None or a<duration)]


def covers(spans,start,end):
    return spans is None or any(a-EPS<=start and end<=b+EPS for a,b in spans)


def clamp_ranges(ranges,spans):
    """Clip incidental subtitle padding/context at seen boundaries; never bridge gaps."""
    if spans is None:return copy.deepcopy(ranges)
    result=[]
    for r in ranges:
        for a,b in spans:
            start,end=max(a,r['start']),min(b,r['end'])
            if end>start+EPS:result.append(dict(r,start=round(start,6),end=round(end,6)))
    return sorted(result,key=lambda r:r['start'])


def scoped_cues(script,manifest,reader=read_srt):
    if script is None:return []
    cues=reader(script,float(manifest.get('transcript_offset_seconds',0)))
    spans=windows(manifest)
    if spans is None:return cues
    # A line crossing the playhead is omitted in full: its text may reveal its ending.
    return [dict(c,id=i+1) for i,c in enumerate(c for c in cues if covers(spans,c['start'],c['end']))]


def scope_events(events,spans):
    if spans is None:return events
    result=[]
    for event in events:
        ranges=event.get('ranges',[])
        # Evidence and sampled frames must be fully seen before context padding is clamped.
        if not any(a-.201<=event['start'] and event['end']<=b+.201 for a,b in spans):continue
        bounded=clamp_ranges(ranges,spans)
        if not bounded or any(not covers(spans,r['start'],r['end']) for r in bounded):continue
        result.append(dict(event,ranges=bounded,start=min(r['start'] for r in bounded),end=max(r['end'] for r in bounded)))
    return result


def scope_timeline(timeline,spans,duration):
    if spans is None:return timeline
    result=[]
    for segment in timeline:
        result.extend(clamp_ranges([segment],spans))
    previous=0
    for a,b in spans:
        if a>previous+EPS:result.append(dict(start=previous,end=a,category='unwatched',basis='unwatched',title='After playback position',why='Hidden after your playback position.'))
        previous=max(previous,b)
    if previous<duration-EPS:result.append(dict(start=previous,end=duration,category='unwatched',basis='unwatched',title='After playback position',why='Hidden after your playback position.'))
    return sorted(result,key=lambda s:s['start'])


def context_cues(event,cues,spans):
    if spans is None:return cues
    start=event['start'];end=event.get('end',max(r['end'] for r in event['ranges']))
    return [c for c in cues if any(a<=start and end<=b and a<=c['start'] and c['end']<=b for a,b in spans)]
