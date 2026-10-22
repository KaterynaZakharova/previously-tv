#!/usr/bin/env python3
"""Explicit, exact-byte shared-asset snapshots in private S3. Never viewer state."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import load_env


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def asset_path(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or len(path.parts) < 2 or path.parts[0] != 'assets' or '..' in path.parts or '\\' in relative or ':' in relative:
        raise ValueError('Unsafe asset path')
    if any(p.startswith('.') for p in path.parts) or path.suffix in ('.jsonl', '.log') or path.parts[1] == 'analysis':
        raise ValueError('Private/log files are excluded from shared snapshots')
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to((root / 'assets').resolve()):
        raise ValueError('Asset path escaped the shared folder')
    return target


def reject_pointer(path):
    if path.suffix.lower() in ('.mp4', '.webm', '.mkv'):
        with path.open('rb') as stream:
            if stream.read(80).startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise ValueError(str(path) + ': Git LFS pointer; run git lfs pull first')


def upload(root, client, bucket, prefix):
    manifest = {'version': 1, 'files': []}
    with tempfile.TemporaryDirectory() as work:
        snapshot = Path(work) / 'db.sqlite'
        database = root / 'assets/db.sqlite'
        if database.is_file():
            with sqlite3.connect('file:' + database.as_posix() + '?mode=ro', uri=True) as source:
                with sqlite3.connect(snapshot) as destination:
                    source.backup(destination)
        for path in sorted((root / 'assets').rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                asset_path(root, relative)
            except ValueError:
                continue
            if path.name.endswith(('-wal', '-shm', '-journal')):
                continue
            reject_pointer(path)
            source = snapshot if relative == 'assets/db.sqlite' else path
            digest = sha256(source)
            key = prefix + '/objects/' + digest
            client.upload_file(str(source), bucket, key, ExtraArgs={
                'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': digest}})
            manifest['files'].append({'path': relative, 'sha256': digest, 'size': source.stat().st_size})
        if not manifest['files']:
            raise ValueError('No shared assets found')
        client.put_object(Bucket=bucket, Key=prefix + '/manifest.json',
                          Body=json.dumps(manifest).encode(), ContentType='application/json', ServerSideEncryption='AES256')
    return {'files': len(manifest['files']), 'manifest': 's3://' + bucket + '/' + prefix + '/manifest.json'}


def download(root, client, bucket, prefix, replace=False):
    body = client.get_object(Bucket=bucket, Key=prefix + '/manifest.json')['Body']
    try:
        manifest = json.loads(body.read())
    finally:
        body.close()
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('Unknown snapshot format')
    targets = []
    seen = set()
    # Validate all destinations before changing any files.
    for row in manifest['files']:
        target = asset_path(root, row['path'])
        digest = row['sha256']
        if not re.fullmatch('[a-f0-9]{64}', digest) or type(row['size']) is not int or row['size'] < 0 or row['path'] in seen:
            raise ValueError('Invalid/duplicate asset checksum entry')
        seen.add(row['path'])
        if target.is_file() and sha256(target) == digest:
            continue
        if target.exists() and not replace:
            raise ValueError(str(target) + ' differs. Stop the server, back up assets/db.sqlite, then use --replace-existing.')
        targets.append((target, row))
    with tempfile.TemporaryDirectory(dir=root) as work:
        staged = []
        for index, (target, row) in enumerate(targets):
            temp = Path(work) / str(index)
            client.download_file(bucket, prefix + '/objects/' + row['sha256'], str(temp))
            if temp.stat().st_size != row['size'] or sha256(temp) != row['sha256']:
                raise ValueError('S3 content does not match snapshot checksum: ' + row['path'])
            # Check using the real destination extension without renaming the file.
            if target.suffix.lower() in ('.mp4', '.webm', '.mkv'):
                with temp.open('rb') as stream:
                    if stream.read(80).startswith(b'version https://git-lfs.github.com/spec/v1'):
                        raise ValueError('S3 snapshot contains an LFS pointer')
            staged.append((temp, target))
        for temp, target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp.replace(target)
    return {'downloaded': len(targets), 'unchanged': len(manifest['files']) - len(targets)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['upload', 'download'])
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--prefix', default='previously/shared')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--replace-existing', action='store_true')
    args = parser.parse_args()
    args.root = args.root.resolve()
    load_env(args.root)
    import boto3
    client = boto3.client('s3', region_name=os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION') or 'us-east-1')
    prefix = args.prefix.strip('/')
    if not prefix:
        parser.error('Use a nonempty prefix')
    result = (upload(args.root, client, args.bucket, prefix) if args.action == 'upload' else
              download(args.root, client, args.bucket, prefix, args.replace_existing))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
