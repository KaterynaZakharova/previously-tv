"""Story facts come exclusively from SQLite. All times use the original source clock."""
from .media_catalog import seconds


def merge_ranges(ranges):
    out=[]
    for a,b in sorted(ranges,key=lambda span:(span[0],span[1])):
        if b<=a:continue
        if out and a<=out[-1][1]+1e-6:out[-1][1]=max(out[-1][1],b)
        else:out.append([a,b])
    return out


def contains(ranges,a,b):
    return any(x<=a+1e-6 and y>=b-1e-6 for x,y in ranges)


def presence_ranges(person):
    for entries in person.get('presence',{}).values():
        for entry in entries:
            for a,b in entry.items():
                try:yield seconds(a),seconds(b)
                except (TypeError,ValueError):continue


def visible_story(media,watched):
    complete=bool(media['episodes']) and all(contains(watched,e['original_start'],e['original_start']+float(e['duration'] or 0)-.01) for e in media['episodes'])
    visible=[]
    for c in media['characters']:
        if not complete and not any(b>x and a<y for a,b in presence_ranges(c) for x,y in watched):continue
        # Free-form biographies have no reveal timestamps: withhold until the whole title is watched.
        visible.append(dict(id='c'+str(c['id']),database_id=c['id'],name=c['name'],photo=c['photo'],
            actor=c['actor']['name'] if c['actor'] else '',description=c['bio'] if complete else '',
            bio_locked=not complete,motivation='',known_facts=[]))
    ids={c['id'] for c in visible};links=[]
    for r in media['relationships']:
        a,b=seconds(r['timestamp_from']),seconds(r['timestamp_to'])
        source,target='c'+str(r['character_from']),'c'+str(r['character_to'])
        if source not in ids or target not in ids or (not complete and not contains(watched,a,b)):continue
        item=dict(id=r['id'],source=source,target=target,label=r['type'],explanation=r['cause'],
            basis='database',at=a,end=b,episode_id=r['episode_id'],history=[dict(at=a,label=r['type'],explanation=r['cause'])])
        links.append(item)
        for c in visible:
            if c['id'] in (source,target):c['known_facts'].append(dict(at=a,text=r['cause']))
    return dict(media_id=media['id'],title=media['title'],characters=visible,relationships=links,
        source='database',spoiler_protection=True,complete=complete)


def tag_events(manifest,events):
    """Use DB appearance/relationship intervals for retrieval, never an inferred AI graph."""
    offset=float(manifest.get('original_start_seconds',0))
    people=[dict(c,presence=c.get('metadata_presence',{})) for c in manifest['characters']]
    for e in events:
        spans=[(offset+r['start'],offset+r['end']) for r in (e.get('ranges') or [dict(start=e['start'],end=e['end'])])]
        e['characters']=[c['id'] for c in people if any(y>a and x<b for x,y in presence_ranges(c) for a,b in spans)]
        e['relationships']=[]
        for r in manifest.get('supplied_relationships',[]):
            x,y=seconds(r['timestamp_from']),seconds(r['timestamp_to'])
            stop=manifest.get('original_end_seconds')
            if any(x<b and y>a for a,b in spans) and (stop is None or y<=stop):
                e['relationships'].append(dict(source='c'+str(r['character_from']),target='c'+str(r['character_to']),label=r['type'],explanation=r['cause'],basis='database'))
    return events
