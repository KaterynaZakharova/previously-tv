# S3 snapshots and SageMaker preparation

These are explicit tools, not requirements for normal playback. Use them to transfer exact files between machines or prepare a larger new catalog without leaving the laptop running.

## Private S3 snapshot

Create a private S3 bucket in your chosen region. Keep Block Public Access enabled. Configure your AWS profile/role and install `requirements-aws.txt`.

```powershell
python tools/aws_assets.py upload --bucket YOUR_BUCKET --prefix previously/shared
```

The upload includes source media, images, transcripts and a consistent SQLite backup of `assets/db.sqlite`. Objects are stored by SHA-256 and the manifest is published last. Uploads request SSE-S3 encryption. It excludes `runtime`, viewer data, logs, dotfiles and `assets/analysis`. It rejects Git LFS pointer videos so an incomplete checkout cannot become a new shared media snapshot.

On another machine, from the project root:

```powershell
python tools/aws_assets.py download --bucket YOUR_BUCKET --prefix previously/shared
```

Unchanged content is skipped. Every downloaded object is size/hash-checked before any destination is replaced. Existing differing files are refused unless `--replace-existing` is explicitly supplied. Stop the Python server and back up `assets/db.sqlite` before restoring a changed catalog. Do not run a restore over an open shared database. Media are restored byte-for-byte so existing prepared source hashes remain valid. S3 is a distribution source here; TV playback still comes from the laptop server.

Use a new prefix for separate catalog revisions. Overwriting a prefix publishes a new manifest; old content-addressed objects are retained until you delete them. This tool does not garbage-collect snapshots or create public playback URLs.

Official SDK references: [S3 upload_file](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/upload_file.html), [download_file](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/download_file.html).

## SageMaker Processing batch

The supplied worker downloads the S3 snapshot, invokes the existing frame/transcript preparation pipeline with Bedrock/Strands, and exports `db.sqlite`, the logbook report and response audit log. It does not train a new model. Already prepared source hashes are reused; to demonstrate fresh inference, supply a genuinely new source/catalog entry rather than deleting useful logbooks.

Build and push an AMD64 ECR image (replace account/repository/region):

```powershell
$Registry = "ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com"
aws ecr create-repository --repository-name previously-processing --region us-east-1
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $Registry
docker buildx build --platform linux/amd64 -f integrations/aws/Dockerfile.processing -t "$Registry/previously-processing:latest" --push .
```

Create a SageMaker execution role trusted by `sagemaker.amazonaws.com` with read access to the input snapshot, write access to the output prefix, Bedrock model invocation, ECR pulls and CloudWatch logging. The submitting identity also needs `sagemaker:CreateProcessingJob` and `iam:PassRole` for that specific role. See [AWS_IAM.md](AWS_IAM.md).

Preview the exact job request, with no resource creation:

```powershell
python tools/submit_aws_processing.py --bucket YOUR_BUCKET --prefix previously/shared --output s3://YOUR_BUCKET/previously/results --image ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/previously-processing:latest --role arn:aws:iam::ACCOUNT_ID:role/PreviouslyProcessingRole --region us-east-1 --movie tears_of_steel_e01 --review-clips
```

Add `--submit` to actually create the job. Defaults: one `ml.m5.xlarge` CPU instance, 30 GB volume, one preparation worker, one-hour maximum runtime, Strands over Bedrock. Use `--engine bedrock` for direct Converse. Omit `--movie` to process every media unit. Repeat it to select multiple units. Choose another instance/volume/container setup for a catalog larger than the default volume.

The worker uses an execution role, not the laptop's `.env`/Nova API key. The image contains code and FFmpeg only; input assets are downloaded at job start. Derived files are exported under the job-specific S3 output location. No personal viewer database is exported.

Monitor in the SageMaker console or with `aws sagemaker describe-processing-job --processing-job-name JOB_NAME`. Download the resulting catalog to a temporary directory:

```powershell
aws s3 cp s3://YOUR_BUCKET/previously/results/JOB_NAME/db.sqlite ./processing-result/db.sqlite
```

Stop the laptop server, back up the current catalog, then replace `assets/db.sqlite` with the output only if it corresponds to the same source-media snapshot. Keep `runtime/viewers.sqlite3`. Start either run mode to use the saved logbooks. Review `result.json`/CloudWatch logs; a failed/partial job must not be treated as a complete catalog. Completed batches persist inside the exported SQLite snapshot when available.

To stop a running job, use `aws sagemaker stop-processing-job --processing-job-name JOB_NAME`. Instance compute stops after the job, but model inference, storage, logs and data transfer are separate usage. No job is submitted merely by starting the TV server.

Official service references: [SageMaker Processing](https://docs.aws.amazon.com/sagemaker/latest/dg/processing-job.html), [CreateProcessingJob](https://docs.aws.amazon.com/boto3/latest/reference/services/sagemaker/client/create_processing_job.html).
