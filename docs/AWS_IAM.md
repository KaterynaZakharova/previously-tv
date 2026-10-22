# Credentials, permissions and operations

Use standard boto3 credential resolution: a locally configured profile/SSO session, environment-provided temporary credentials, or an AWS workload execution role. Do not put access keys in the APK, a catalog row, a prompt, or a committed `.env`. The direct Nova developer key and AWS IAM credentials are separate authentication paths. `AWS_PROFILE=previously` is useful locally; omit it in managed-runtime containers so the execution role resolves normally.

## Laptop: direct Bedrock/Strands

For the supplied non-streaming transports, allow `bedrock:InvokeModel` on your chosen foundation-model ARN or inference-profile ARN and any underlying regional models required by that profile. Example for the default direct model; replace the region/model as needed:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:InvokeModel"],
    "Resource": ["arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"]
  }]
}
```

Configured caption review models need equivalent permission. The application does not need model-listing permission. `check_ai.py --test` checks actual inference access; a populated model setting alone does not verify IAM permissions, login freshness or regional availability.

## Laptop: AgentCore client

Use `bedrock-agentcore:InvokeAgentRuntime` scoped to the chosen runtime and endpoint ARNs. For example, replace account/region/runtime ID:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
    "Resource": [
      "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT_ID:runtime/RUNTIME_ID",
      "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT_ID:runtime/RUNTIME_ID/runtime-endpoint/DEFAULT"
    ]
  }]
}
```

Verify resource authorization against the current [AgentCore resource-policy guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-based-policies.html), especially if using a custom endpoint/qualifier. The AgentCore execution role itself needs Bedrock invocation permission plus AWS's runtime/ECR/logging permissions; see [runtime permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).

## S3 snapshot tools

An uploader needs `s3:PutObject` under `arn:aws:s3:::YOUR_BUCKET/previously/shared/*`; a downloader needs `s3:GetObject` under that same prefix. The snapshot helper knows exact object keys and does not need bucket listing. It requests AES256 server-side encryption and uses private objects. A bucket policy that requires a customer-managed KMS key needs corresponding changes to the helper's encryption arguments and KMS permissions; that variant is not configured here.

## SageMaker Processing roles

The execution role needs:

- Trust for `sagemaker.amazonaws.com`, constrained to your account as appropriate.
- `s3:GetObject` on the input snapshot prefix, and `s3:PutObject` on the selected output prefix. Add bucket-level permissions required by SageMaker's output handling.
- The same scoped `bedrock:InvokeModel` access as direct inference.
- ECR image pull permissions and CloudWatch log delivery permissions required by SageMaker.

The deploying/submitting identity is separate. It needs ECR push permissions if publishing an image, AgentCore control permissions if creating/updating a runtime, `sagemaker:CreateProcessingJob`/monitor/stop as needed, and `iam:PassRole` restricted to the chosen execution roles. Do not use an app-runtime role for broad deployment administration. Service setup does not happen automatically in this project.

## Logs and retained files

`runtime/logs/ai-responses.jsonl` contains provider requests/responses, repair errors and request identifiers. Direct Nova redacts its configured developer key; AWS code never serializes SDK session/credential objects. Logs may contain film dialogue, sampled evidence, feedback context and AI output, so treat them as application data. Strands binary frames in its conversation log are represented by a label. Managed containers also emit responses on stdout for service log collection.

`runtime/cache` and `runtime/artifacts` are regenerated successful model results and rendered clips. `assets/db.sqlite` is shared source knowledge; `runtime/viewers.sqlite3` is personal watch/feedback state. S3 helpers exclude personal state and audit logs. Do not commit keys, generated runtime, `.venv`, AWS local config or account-specific deployment JSON.

On failure, verify SDK installation, Python version, active profile/login, region, model access and ARN/qualifier in that order. SDK/service errors are logged and exposed as unavailable/invalid AI; there is no silent developer-API fallback. Remove unused AgentCore runtimes, SageMaker jobs, ECR images, S3 snapshots and log groups when finished according to your retention needs.
