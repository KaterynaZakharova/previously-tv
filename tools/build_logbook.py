#!/usr/bin/env python3
"""Analyze full media once, persist validated actions/edits in assets/db.sqlite.
Already completed sources and validated batches are reused automatically.
"""
import argparse,json,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.catalog import Catalog,ROOT
from backend.ai import make_ai
from backend.pipeline import Pipeline
from backend.logbook import build
from backend.logbook_review import refine

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--movie',action='append',help='Unit ID; omit to analyze all media')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=2,help='Concurrent source preparations; default 2, use 1 for tight quotas')
    parser.add_argument('--review-clips',action='store_true',help='Optional additional Nova Pro pass for per-frame retained-clip captions')
    args=parser.parse_args()
    # Same optional local environment file as the server, never required for reuse.
    env=args.root/'.env'
    if env.is_file():
        import os
        for line in env.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
    catalog=Catalog(args.root);runtime=args.root/'runtime';ai=make_ai(runtime);pipeline=Pipeline(catalog,ai,runtime)
    selected=args.movie;manifests=catalog.manifests()
    if selected and set(selected)-{m['id'] for m in manifests}:parser.error('Unknown movie/unit ID')
    def prepare(m):
        worker_ai=make_ai(runtime);worker_ai.log_lock=ai.log_lock
        worker=Pipeline(catalog,worker_ai,runtime)
        try:
            result=build(worker,m,lambda stage,msg:print(stage+': '+msg,flush=True))
            if args.review_clips:result=refine(worker,m,result,lambda stage,msg:print(stage+': '+msg,flush=True))
            row=dict(unit=m['id'],actions=len(result['timeline']),recap_moments=len(result['events']),model=result['model'],source_key=result['source_key'])
            print(json.dumps(row),flush=True)
            return row,None
        except Exception as error:
            worker_ai.log(dict(task='build_logbook',status='failed',movie=m['id'],error=str(error)))
            print(m['id']+': '+str(error),file=sys.stderr,flush=True)
            return None,dict(unit=m['id'],error=str(error))
    targets=[m for m in manifests if not selected or m['id'] in selected]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:outcomes=list(pool.map(prepare,targets))
    results=[r for r,e in outcomes if r];failed=[e for r,e in outcomes if e]
    from tools.export_logbook_report import export
    export(args.root)
    print(json.dumps(dict(completed=results,failed=failed)),flush=True)
    return 1 if failed else 0
if __name__=='__main__':raise SystemExit(main())
