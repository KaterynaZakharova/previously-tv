---
inclusion: always
---
# Previously project rules

This is a Fire TV WebView app, with a Python backend and buildless HTML frontend.
Preserve remote focus/navigation, large TV text and the current playback/recap flows.

Maintain both run modes. Local is Python 3.9+ stdlib-only and must never import AWS
SDKs; optional AWS code uses Python 3.10+. Route AI through backend.ai.make_ai.
Use Nova via the developer API locally or Bedrock/Strands/AgentCore in AWS mode.
Never silently change transport, invent AI output or use a failed response as data.

Story-map data come from assets/db.sqlite. Preserve watched-only visibility and
all evidence/time-range validators. Remember keeps the queue; not-remember/dwell
can expand context. Clip edits remain within watched source ranges and 7-45 seconds.

Never include .env, access keys, raw runtime viewer state, logs or media in model
instructions for a coding task. Deployment must be explicit; tools that create
paid resources keep a dry-run/default preview where provided.

Before a PR: run Python unittest discovery and the four Node smoke tests listed
in docs/AWS_VALIDATION.md. Use SDK contract doubles unless live AWS inference is
explicitly requested. Document any changed integration/configuration in docs/.
