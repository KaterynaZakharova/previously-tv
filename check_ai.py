#!/usr/bin/env python3
"""Check configured provider access without exposing credentials. Python stdlib only."""
import argparse,json
from pathlib import Path
from backend.ai import make_ai,AIUnavailable,AIResponseInvalid,is_configured
from server import load_env

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--list-models',action='store_true',help='List configured model IDs (not proof of access)')
    parser.add_argument('--test',action='store_true',help='Make one small structured-output test request (provider quotas apply)')
    args=parser.parse_args();root=Path(__file__).resolve().parent
    load_env(root);ai=make_ai(root/'runtime')
    print(json.dumps(dict(provider=ai.label,model=ai.model,endpoint=ai.endpoint,key_environment_variable=ai.key_env,key_configured=is_configured(ai))))
    try:
        if args.list_models:
            print(json.dumps(dict(available_models=ai.list_models()),ensure_ascii=False))
        if args.test:
            schema=dict(type='object',properties=dict(ok=dict(type='boolean'),message=dict(type='string')),required=['ok','message'],additionalProperties=False)
            result=ai.json('connection_test','Return ok=true and a short message confirming structured output works.',{},schema,max_tokens=250)
            if result['ok'] is not True:raise ValueError('Provider did not confirm the test')
            print(json.dumps(dict(status='passed',provider=ai.label,model=ai.model,result=result),ensure_ascii=False))
        return 0
    except (AIUnavailable,AIResponseInvalid,ValueError) as exc:
        message=str(exc).replace(ai.key,'[REDACTED]') if ai.key else str(exc)
        print(json.dumps(dict(status='failed',error=message)));return 1

if __name__=='__main__':raise SystemExit(main())
