"""Small provider-neutral JSON schemas. Semantic checks live in pipeline.py."""
def obj(**fields):
    return dict(type='object',properties=fields,required=list(fields),additionalProperties=False)
def arr(item):return dict(type='array',items=item)
S=dict(type='string');I=dict(type='integer')
RANGE=obj(first=I,last=I)
REL=obj(source=S,target=S,label=S,explanation=S)
EVENT=obj(title=S,summary=S,category=dict(type='string',enum=['critical','secondary','filler']),
          why=S,characters=arr(S),threads=arr(S),location=S,evidence=S,ranges=arr(RANGE),relationships=arr(REL))
ANALYSIS=obj(events=arr(EVENT),coverage=arr(obj(first=I,last=I,category=dict(type='string',enum=['critical','secondary','filler','unclassified']),why=S)))
PROFILES=obj(characters=arr(obj(id=S,description=S,motivation=S)))
QUEUE=obj(explanation=S,items=arr(obj(event_id=S,reason=S,ranges=arr(RANGE))))

REVIEW=obj(events=arr(obj(index=I,supported=dict(type='boolean'),reason=S,unsupported_relationships=arr(I))),
           unsupported_coverage=arr(I))

def review_for(events,coverage):
    return obj(events=arr(obj(index=dict(type='integer',enum=list(range(len(events)))),
        supported=dict(type='boolean'),reason=S,unsupported_relationships=arr(I))),
        unsupported_coverage=arr(dict(type='integer',enum=list(range(len(coverage))))))

def queue_for_candidates(ids):
    # Constrained decoding must copy a REAL candidate ID, including zero padding.
    return obj(explanation=S,items=arr(obj(event_id=dict(type='string',enum=list(ids)),reason=S,ranges=arr(RANGE))))

GAPS=obj(assessments=arr(obj(index=I,category=dict(type='string',enum=['critical','secondary','filler','unclassified']),
                              why=S,evidence_cue_ids=arr(I))))

def coverage_for_cues(ids):
    return obj(classifications=arr(obj(cue_id=dict(type='integer',enum=list(ids)),
        category=dict(type='string',enum=['critical','secondary','filler','unclassified']),why=S,evidence=S)))

N=dict(type='number')
SCENES=obj(scenes=arr(obj(index=I,category=dict(type='string',enum=['critical','secondary','filler','unclassified']),
    title=S,summary=S,why=S,characters=arr(S),visual_evidence=S,evidence_cue_ids=arr(I))))
def scenes_for(castids,indexes,cueids):
    import copy
    schema=copy.deepcopy(SCENES);row=schema['properties']['scenes']['items']['properties']
    # S/I are shared schema constants. Replace branches; mutating a shared leaf
    # would accidentally restrict titles to cast IDs and scene indexes to cues.
    row['index']=dict(type='integer',enum=list(indexes))
    row['characters']=arr(dict(type='string',enum=sorted(castids))) if castids else dict(type='array',items=dict(type='string'),maxItems=0)
    row['evidence_cue_ids']=arr(dict(type='integer',enum=sorted(cueids))) if cueids else dict(type='array',items=dict(type='integer'),maxItems=0)
    return schema
def time_queue_for_candidates(ids):
    return obj(explanation=S,items=arr(obj(event_id=dict(type='string',enum=list(ids)),reason=S,
        ranges=arr(obj(start=N,end=N)))))

def cast_observations(castids,targets,indexes):
    return obj(observations=arr(obj(scene_index=dict(type='integer',enum=list(indexes)),
        character=dict(type='string',enum=sorted(targets)),frame_time=N,
        companions=arr(dict(type='string',enum=sorted(castids))),visual_evidence=S,
        category=dict(type='string',enum=['critical','secondary','filler']),why=S)))
