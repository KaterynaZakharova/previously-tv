#!/usr/bin/env python3
"""Prepare or explicitly submit a SageMaker Processing request"""

import argparse
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import load_env


def request_for(args):
    if not args.output.startswith('s3://') or not args.prefix.strip('/'):
        raise ValueError('Use an s3:// output URI and a nonempty asset prefix')
    if (
        not re.fullmatch(r'[A-Za-z0-9](-*[A-Za-z0-9])*', args.name)
        or len(args.name) > 63
    ):
        raise ValueError('Invalid SageMaker job name')
    return {
        'ProcessingJobName': args.name,
        'RoleArn': args.role,
        'AppSpecification': {'ImageUri': args.image},
        'ProcessingResources': {
            'ClusterConfig': {
                'InstanceCount': 1,
                'InstanceType': args.instance_type,
                'VolumeSizeInGB': 30,
            }
        },
        'StoppingCondition': {'MaxRuntimeInSeconds': 3600},
        'Environment': {
            'RUN_MODE': 'aws',
            'AWS_AI_ENGINE': args.engine,
            'AWS_REGION': args.region,
            'AWS_BEDROCK_MODEL_ID': args.model,
            'PREVIOUSLY_ASSET_BUCKET': args.bucket,
            'PREVIOUSLY_ASSET_PREFIX': args.prefix.strip('/'),
            'PREVIOUSLY_UNITS': json.dumps(args.movie or []),
            'PREVIOUSLY_REVIEW_CLIPS': '1' if args.review_clips else '0',
        },
        'ProcessingOutputConfig': {
            'Outputs': [
                {
                    'OutputName': 'logbook',
                    'S3Output': {
                        'S3Uri': args.output.rstrip('/') + '/' + args.name,
                        'LocalPath': '/opt/ml/processing/output',
                        'S3UploadMode': 'EndOfJob',
                    },
                }
            ]
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--prefix', default='previously/shared')
    parser.add_argument('--output', required=True)
    parser.add_argument(
        '--image', required=True, help='ECR URI of Dockerfile.processing image'
    )
    parser.add_argument('--role', required=True, help='SageMaker execution role ARN')
    parser.add_argument(
        '--name',
        default='previously-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'),
    )
    parser.add_argument('--movie', action='append')
    parser.add_argument('--review-clips', action='store_true')
    parser.add_argument('--instance-type', default='ml.m5.xlarge')
    parser.add_argument('--engine', choices=['bedrock', 'strands'], default='strands')
    parser.add_argument('--model', default='amazon.nova-pro-v1:0')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument(
        '--submit',
        action='store_true',
        help='Actually create a billable Processing job',
    )
    args = parser.parse_args()
    load_env(Path(__file__).resolve().parents[1])
    request = request_for(args)
    print(json.dumps(request, indent=2))
    if args.submit:
        import boto3

        result = boto3.client(
            'sagemaker', region_name=args.region
        ).create_processing_job(**request)
        print(json.dumps({'ProcessingJobArn': result['ProcessingJobArn']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
