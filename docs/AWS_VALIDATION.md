# AWS integration validation

This is a source/SDK-tested integration, not a claim of deployment to a live AWS account. No AWS resources, SageMaker jobs or paid model requests were created during this update. Existing frontend/Android source and the shared SQLite catalog were preserved.

## Checks

- The original 195 Python tests pass after the transport refactor.
- Twenty added integration checks cover explicit mode selection, optional AWS imports, binary frame/label/timecode forwarding, tool-schema preservation, response caching, region/engine isolation, review transport, malformed/truncated output, bounded repairs, throttling, remote-model/tool restrictions, and AgentCore client/server protocol.
- SDK checks use real boto3 service shapes, the installed Strands Agent/BedrockModel/Pydantic structured-output loop, and the AgentCore SDK `/ping` and `/invocations` HTTP surface. Only service-network operations use deterministic doubles. Deployment and Processing request objects validate against the installed boto3 service models.
- S3 tests cover exact-byte media/catalog round trips, excluding personal state/logs, LFS pointer rejection, unsafe paths, and hash verification before replacing destinations.
- All four existing Node interaction harnesses pass: Home, queue navigation/memory feedback, recap controller and full-screen feed. The UI/APK did not change.
- The Processing submit command was run in its default dry-run mode; it produced a request without importing/using a live AWS client.

Installed direct AWS dependencies for SDK validation: boto3 1.43.117, strands-agents 1.62.0 and bedrock-agentcore 1.24.2, on Python 3.12. The local dependency-free environment skips six SDK-only tests; all remaining tests run without AWS packages.

## Reproduce

```powershell
python -m unittest discover -s tests -v
node tests/home_ui_smoke.cjs
node tests/ui_smoke.cjs
node tests/feed_controller.cjs
node tests/feed_ui_smoke.cjs
```

With AWS dependencies installed, run the same Python command to include the six real-SDK contract checks. These tests do not require credentials or call AWS. To check a real account, configure your profile/region/model and run `python check_ai.py --test`; that command is a live request and consumes provider quota/usage.

## Still to verify in your account

Bedrock model/regional access, actual multimodal model behavior on a new source, the ARM64/AMD64 Docker builds, ECR image pulls, role permissions, live AgentCore latency/log collection, and a complete SageMaker Processing run. Docker/cloud deployment were not executed here. Existing prepared catalog recaps intentionally bypass fresh inference, so use logs and the connection test to distinguish stored output from a new provider response.

The SDK and deterministic tests establish request/response compatibility and app behavior, not the factual quality of every future model response. Existing evidence, watched-range and clip validators remain enabled in either mode.

## Voice extension validation

The extended project passes 228 Python tests with the optional AWS packages installed, including the thirteen voice/backend/speech tests. Without those packages, six SDK-only checks are skipped. Five Node interaction harnesses pass, including `tests/voice_ui_smoke.cjs` and a real-feed pause/dwell check. No live Nova/Polly/Transcribe call was made. Android source/manifest are supplied but compilation was blocked by the Gradle distribution network download; see [VOICE_CHAT.md](VOICE_CHAT.md).
