# Two run options

Both options serve the same TV app. The APK continues connecting to the laptop's Python server. The launcher chooses inference; it does not migrate/delete SQLite data, change progress, or recreate media.

| | Local / without AWS account | With AWS |
|---|---|---|
| Launcher | `python run.py --mode local` | `python run.py --mode aws` |
| Python | 3.9+ | 3.10+, recommend 3.12 |
| Extra pip packages | None | `requirements-aws.txt` |
| Prepared demo playback/recaps | No model/key required | Reuses the same stored analysis without a model call |
| New-source analysis | Optional Nova developer API key | AWS credentials + a Bedrock Nova vision model |
| New-source images | Timestamped sampled frames | Same timestamped sampled frames |
| Persistence and editing | SQLite + FFmpeg | Same; optional S3 snapshot/SageMaker batch |

“Without AWS” means without an AWS account, IAM or AWS SDK. The optional direct Nova developer API is still an Amazon-hosted service; it is not an offline model. With no key, prepared annotations support normal demo recaps, while unprepared sources report AI unavailable. No synthetic AI output is substituted.

## Install and run locally on Windows

Keep the existing MP4 files and `assets/db.sqlite`. If media are 130-byte Git LFS pointers, run `git lfs install` and `git lfs pull` in the repository root before testing.

Install FFmpeg/FFprobe on PATH. From the directory containing `server.py`:

```powershell
Copy-Item .env.local.example .env
python run.py --mode local --host 0.0.0.0 --port 8000
```

Copy the example only when creating a new `.env`; preserve an existing key/configuration. Leave `NOVA_API_KEY` blank for prepared demo playback. For analyzing new sources, set it to your Nova developer key, then:

```powershell
python check_ai.py --test
python tools/build_logbook.py --workers 1 --review-clips
```

`server.py` still works directly and defaults to local mode. `RUN_MODE` in `.env` also selects the mode for tools such as `build_logbook.py` and `check_ai.py`.

## Install and run with AWS on Windows

Use a separate Python environment if the laptop currently runs Python 3.9:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-aws.txt
```

Install AWS CLI v2 from AWS's official installer. Configure an authorized local profile, for example with IAM Identity Center:

```powershell
aws configure sso --profile previously
aws sso login --profile previously
aws sts get-caller-identity --profile previously
```

On first setup, copy `.env.aws.example` to `.env`. Set `AWS_PROFILE`, `AWS_REGION` and a Nova **Bedrock** model/inference-profile ID available in that region. The example `amazon.nova-pro-v1:0` is configurable, not a promise of regional access. Developer API IDs such as `nova-pro-v1` are not Bedrock IDs.

```powershell
python check_ai.py --test
python run.py --mode aws --aws-engine strands --host 0.0.0.0 --port 8000
```

For fewer moving parts, choose `--aws-engine bedrock`. Both call Nova on Bedrock. `strands` uses the Strands agent and Pydantic structured-output loop; `bedrock` sends the same schema via Converse tool use. `agentcore` additionally requires the endpoint in [AWS_AGENTCORE.md](AWS_AGENTCORE.md).

Before preparing new sources, stop the server if editing the shared catalog, back up `assets/db.sqlite`, set `RUN_MODE=aws` and your engine in `.env`, then:

```powershell
python tools/build_logbook.py --workers 1 --review-clips
```

The CLI mode argument applies only to that launcher process; it does not rewrite `.env`. To override the mode for other tools in the current PowerShell session:

```powershell
$env:RUN_MODE = "aws"
$env:AWS_AI_ENGINE = "strands"
python check_ai.py --test
```

Unset session overrides before relying on `.env` again: `Remove-Item Env:RUN_MODE` and `Remove-Item Env:AWS_AI_ENGINE`. Environment values take precedence over `.env`.

## Verify locally and on Fire TV

Open `http://localhost:8000/?tv=1`. Continue episode 2, open Previously, test Down/Up feedback, scene notes with Left, and the watched-time character map with Right. Remember keeps the queue; not-remember/dwell requests more available context. Stored-logbook selection remains local in both modes.

The existing APK needs no rebuild for this backend-only change. Keep Python running, connect the TV to the same LAN and use `http://LAPTOP_LAN_IP:8000` as the wrapper's server URL. Allow port 8000 through the laptop's private-network firewall. Use Android Studio's existing debug build/install instructions in `android/README.md` if reinstalling. The TV receives no cloud credential.

Use `python check_ai.py --test` to make one small **live, quota/billing-applicable** test. `--list-models` in AWS mode lists only configured model IDs; it does not discover/access-check your account's model catalog. Watching existing demo recaps alone does not prove a new AWS model request happened. Check the JSONL response log for the actual provider/task.
