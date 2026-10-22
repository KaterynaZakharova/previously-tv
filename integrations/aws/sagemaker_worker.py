"""SageMaker Processing batch: shared S3 snapshot -> validated SQLite logbook."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    import boto3
    from tools.aws_assets import download

    root = Path('/app')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    client = boto3.client('s3', region_name=region)
    download(
        root,
        client,
        os.environ['PREVIOUSLY_ASSET_BUCKET'],
        os.environ['PREVIOUSLY_ASSET_PREFIX'],
    )
    command = [
        sys.executable,
        'tools/build_logbook.py',
        '--root',
        str(root),
        '--workers',
        '1',
    ]
    for unit in json.loads(os.environ.get('PREVIOUSLY_UNITS', '[]')):
        command.extend(['--movie', unit])
    if os.environ.get('PREVIOUSLY_REVIEW_CLIPS') == '1':
        command.append('--review-clips')
    result = subprocess.run(command, cwd=root)
    output = Path('/opt/ml/processing/output')
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'assets/db.sqlite', output / 'db.sqlite')
    for source in (
        root / 'assets/analysis/logbook_report.json',
        root / 'runtime/logs/ai-responses.jsonl',
    ):
        if source.is_file():
            shutil.copy2(source, output / source.name)
    (output / 'result.json').write_text(json.dumps({'exit_code': result.returncode}))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
