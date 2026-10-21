#!/usr/bin/env python3
"""Export the saved run summary and response-only audit, without another AI call."""
import json,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.catalog import Catalog,ROOT
from backend.logbook import Logbook

def export(root=ROOT):
    catalog=Catalog(root);out=root/'assets/analysis';out.mkdir(parents=True,exist_ok=True);units=[]
    for m in catalog.manifests():
        stored=Logbook(catalog).load(m)
        if not stored:
            units.append(dict(unit=m['id'],status='not_prepared'));continue
        lengths=[round(sum(r['end']-r['start'] for r in e['ranges']),3) for e in stored['events']]
        categories=Counter()
        for t in stored['timeline']:categories[t['category']]+=t['end']-t['start']
        units.append(dict(unit=m['id'],status='prepared',caption_review_status='complete' if stored.get('visual_caption_review')==2 else 'partial' if stored.get('visual_caption_review')==1 else 'optional_not_complete',caption_reviewed_events=stored.get('caption_reviewed_events',len(stored['events']) if stored.get('visual_caption_review')==2 else 0),caption_model=stored.get('caption_model'),visual_caption_review=stored.get('visual_caption_review'),provider=stored['provider'],model=stored['model'],created_at=stored['created_at'],
            source_key=stored['source_key'],has_transcript=bool(m.get('transcript')),duration_seconds=stored['duration'],
            action_beats=len(stored['timeline']),recap_moments=len(lengths),clip_lengths_seconds=lengths,
            category_seconds={k:round(v,3) for k,v in categories.items()},sampling_interval_seconds=stored['sampling_interval_seconds']))
    report=dict(generated_at=datetime.now(timezone.utc).isoformat(),units=units,
        note='Actual saved Nova outputs. Timestamped frame sampling is approximate; labels are AI relevance estimates, not exhaustive motion assessment. Invalid proposed edits were omitted or replaced by valid complete AI-authored beats; no synthetic film facts were inserted.')
    (out/'logbook_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    source=root/'runtime/logs/ai-responses.jsonl'
    if source.is_file():
        records=[]
        for line in source.read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            # Preserve every response/error/validation decision. Exclude requests
            # containing large image data and retain their audit identifiers.
            records.append({k:v for k,v in row.items() if k!='request'})
        (out/'ai-responses.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
    return report
if __name__=='__main__':print(json.dumps(export(),indent=2))
