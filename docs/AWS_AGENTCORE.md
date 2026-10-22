# Optional AgentCore inference endpoint

Bedrock/Strands can run directly from the laptop; AgentCore is optional. It hosts the inference adapter only. Movies, FFmpeg cuts, catalog and per-viewer state stay on the Python server. The runtime uses its execution role to invoke Bedrock. The laptop calls the endpoint with IAM-authenticated boto3; the TV never calls AWS directly.

## Test the endpoint locally

Install `requirements-aws.txt` in a Python 3.10+ environment, configure AWS credentials, select a model and run from the project root:

```powershell
$env:AWS_BEDROCK_MODEL_ID = "amazon.nova-pro-v1:0"
$env:AWS_AGENTCORE_ENGINE = "strands"
python -m integrations.aws.agentcore_app
```

The SDK exposes `GET /ping` and `POST /invocations` on port 8080. A local HTTP invocation still makes a live Bedrock request unless the transport is replaced in a test. `tests/test_aws_integrations.py` exercises the protocol with deterministic doubles without a paid call.

The wire body is `{ "protocol": 1, "request": <Nova.build_payload output> }`. The endpoint accepts the structured `emit_result` operation only. It rejects external image URLs, unsupported roles, excessive images, unapproved model IDs, output limits above 8192 tokens and nested AgentCore routing. Allowed main/review models are set on the endpoint, not freely chosen by callers. Shape/evidence validation and repair remain authoritative on the laptop after response normalization.

## Build and deploy an ARM64 image

Requirements: Docker with buildx, AWS CLI v2, an ECR repository, and an AgentCore execution role. The identity performing deployment needs the related ECR/AgentCore control permissions and `iam:PassRole`; these permissions are separate from the runtime's inference permissions. Use your actual account, region and repository values.

From PowerShell in the project root, replace `ACCOUNT_ID`:

```powershell
$AwsRegion = "us-east-1"
$Registry = "ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com"
aws ecr create-repository --repository-name previously-agent --region $AwsRegion
aws ecr get-login-password --region $AwsRegion | docker login --username AWS --password-stdin $Registry
docker buildx build --platform linux/arm64 -f integrations/aws/Dockerfile.agentcore -t "$Registry/previously-agent:latest" --push .
```

AgentCore uses ARM64; the Processing image in the separate guide uses AMD64. The small inference image copies only the transport modules and SDK dependencies. `.dockerignore` excludes keys, runtime and all media/catalog assets.

Copy `integrations/aws/runtime.example.json` to `integrations/aws/runtime.json`. Replace the image URI, role ARN and model ID. The role trust principal is `bedrock-agentcore.amazonaws.com`. Follow [AWS's current execution-role requirements](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html), including ECR image pulls, runtime logging and Bedrock model invocation. See [AWS_IAM.md](AWS_IAM.md).

```powershell
aws bedrock-agentcore-control create-agent-runtime --cli-input-json file://integrations/aws/runtime.json --region $AwsRegion
```

Record the returned `agentRuntimeArn`; wait for the runtime to reach `READY` using the console or `get-agent-runtime`. No unauthenticated public endpoint is enabled by this template. Configure the laptop `.env`:

```dotenv
RUN_MODE=aws
AWS_AI_ENGINE=agentcore
AWS_REGION=us-east-1
AWS_BEDROCK_MODEL_ID=amazon.nova-pro-v1:0
AWS_AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:REGION:ACCOUNT:runtime/YOUR_RUNTIME_ID
AWS_AGENTCORE_QUALIFIER=DEFAULT
```

The laptop and endpoint must agree on permitted model IDs. To use a separate review model, set `AWS_BEDROCK_REVIEW_MODEL_ID` on both. The endpoint's `AWS_AGENTCORE_ENGINE` selects `strands` or `bedrock` independently of the laptop's `agentcore` value.

Run `python check_ai.py --test`, then `python run.py --mode aws --aws-engine agentcore --host 0.0.0.0`. For new-source preparation, `python tools/build_logbook.py --workers 1 --review-clips` uses the same endpoint. Each evidence task receives a fresh UUID runtime session; there is no persistent model conversation shared among viewers.

## Logs, errors and removal

Model-response content is recorded in the laptop JSONL log. Endpoint SDK/model responses are also written to stdout with `PREVIOUSLY_AUDIT_STDOUT=1`, allowing managed CloudWatch log collection. Binary frame blocks in Strands conversation logs are replaced with a label; response text/tool arguments remain available. Retention, tracing and Transaction Search are account-level choices; this code does not enable them automatically.

If access/model/region is wrong, the task fails explicitly. The server never silently retries through the direct Nova developer API. For account deployment changes, update the runtime configuration/image using the AgentCore console or API. Remove the runtime and any unused custom endpoints/images through the console when done; S3 buckets, ECR image storage and CloudWatch logs can remain separately billable until cleaned up.

Official contracts: [CreateAgentRuntime](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_agent_runtime.html), [InvokeAgentRuntime](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html), [runtime setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html).
