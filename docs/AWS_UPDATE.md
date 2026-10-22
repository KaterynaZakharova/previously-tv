# Apply this update

`previously_aws_update.zip` contains only changed/new code, examples, tests and docs. Extract it over the directory containing `server.py`. It does not contain movies, a replacement catalog, keys or personal runtime state. Keep your existing `.env`, `assets/db.sqlite`, media and `runtime`.

`previously_tv_ai_fix.zip` is the complete small code/assets package with this integration included, still without MP4s. Its shared catalog is unchanged from the preceding release. Existing installations should use the changed-files update to avoid overwriting their catalog changes.

Local mode remains the default. Restart Python after extracting. See [RUN_MODES.md](RUN_MODES.md) for explicit local/AWS commands and AWS dependency installation. No APK rebuild is needed for these backend changes.
