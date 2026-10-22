# Optional Kiro and Kiro Crew workflow

Kiro and Kiro Crew are development tools, not media-analysis services. This release provides repository steering and a concrete review task; it does not claim the code was generated, deployed or evaluated by Kiro, and it does not install a persistent Crew process.

Open the repository in Kiro. `.kiro/steering/previously.md` supplies project rules: preserve both run modes, watched-only output, database-based maps, remote navigation, schema validation and optional AWS imports. Follow [Kiro steering documentation](https://kiro.dev/docs/steering/) for your installed version.

Suggested task for Kiro/Kiro Crew after connecting this repository through your own setup:

> Review the AWS transport change. Trace a timestamped frame/transcript task through local Nova and AWS Bedrock/Strands/AgentCore. Verify caption review keeps the selected transport, malformed responses cannot become clips, and local mode runs with AWS imports blocked. Run the documented Python and Node checks. Produce a patch and a validation report; do not create AWS resources or start new media/model jobs.

For Kiro Crew, follow the [official project setup](https://github.com/kirodotdev/KiroCrew) / current Kiro Crew docs, select the repository workspace, and use the same task with the steering file. Keep credentials in your normal local/AWS profile; never paste them into tasks. Workspace installation, persistent processes and account connections remain optional user configuration.

Useful review targets: `backend/aws_ai.py`, `backend/logbook_review.py`, `tests/test_aws_integrations.py`, `integrations/aws`, and `docs/RUN_MODES.md`. The app itself does not call the Kiro API or require Kiro at runtime.
