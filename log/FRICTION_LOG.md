# Previously TV — Friction Log

## 01. Fire TV microphone access

- **Task:** Capture spoken questions in the Fire TV app.
- **Steps taken:** Opened voice chat and tested wired headphones with a microphone.
- **Expected vs. actual result:** Expected: record a question. Actual: no app speech service was available, and the wired headset did not resolve it.
- **Severity rating:** High.
- **Workaround used:** Used a phone microphone successfully.
- **Actionable suggestion:** Document microphone compatibility by device and provide a supported phone-microphone example.

## 02. Robotic speech output

- **Task:** Read assistant responses in a natural voice.
- **Steps taken:** Tested the existing speech output and explored alternatives.
- **Expected vs. actual result:** Expected: natural speech. Actual: the voice sounded robotic.
- **Severity rating:** Medium.
- **Workaround used:** Kept the existing voice; the quality issue remains unresolved.
- **Actionable suggestion:** Provide voice previews and clearer guidance for selecting natural voices or configuring Polly.

## 03. JDK version mismatch

- **Task:** Build the Fire OS APK.
- **Steps taken:** Ran the Android Gradle build with JDK 11.
- **Expected vs. actual result:** Expected: build an APK. Actual: the Android Gradle plugin required Java 17.
- **Severity rating:** Critical.
- **Workaround used:** Switched to JDK 17, which resolved the build error.
- **Actionable suggestion:** State compatible JDK, Gradle, and Android plugin versions together and provide a version preflight check.

## 04. Slow Nova request cycle

- **Task:** Analyze footage and update recaps responsively.
- **Steps taken:** Made repeated Nova analysis and recommendation requests.
- **Expected vs. actual result:** Expected: shorter waits. Actual: requests appeared to take approximately a minute per call or between calls; the cause was not conclusively isolated.
- **Severity rating:** High.
- **Workaround used:** Removed fixed client delays, honored Retry-After, bounded retries, cached analysis, and selected prepared recap clips locally.
- **Actionable suggestion:** Provide diagnostics separating inference time, quota throttling, retries, and client-imposed delays.

## 05. Evidence quote formatting

- **Task:** Validate dialogue evidence from Nova.
- **Steps taken:** Compared generated evidence quotes with source transcript cues.
- **Expected vs. actual result:** Expected: matching words pass validation. Actual: added quotation marks and subtitle line joins caused the local validator to reject an HTTP 200 response.
- **Severity rating:** High.
- **Workaround used:** Normalized formatting while preserving word order and cue bounds; reconstructed stored quotes from source text.
- **Actionable suggestion:** Provide extraction examples using stable cue IDs and offsets, with explicit quote-normalization rules.

## 06. Unsupported plot claims

- **Task:** Generate accurate event summaries and relationship explanations.
- **Steps taken:** Checked generated claims against their cited dialogue.
- **Expected vs. actual result:** Expected: citations support each claim. Actual: “Cut!” was cited as an operation-abort decision although it referred to stopping filming.
- **Severity rating:** High.
- **Workaround used:** Added separate claim review, bounded corrections, and omission of unsupported events; used supplied database facts for the story map.
- **Actionable suggestion:** Provide grounding evaluations covering ambiguous dialogue, speaker identity, ownership, and causal claims.

## 07. Structured-output validation

- **Task:** Process schema-valid scene analysis, including silent scenes.
- **Steps taken:** Requested structured results and retried invalid output.
- **Expected vs. actual result:** Expected: valid scene-specific evidence arrays. Actual: validation failed after repairs, including evidence_cue_ids exceeding maxItems: 0 for a silent scene.
- **Severity rating:** High.
- **Workaround used:** Supplied allowed cue lists, removed invalid references before schema validation, bounded repairs, and discarded invalid cache entries.
- **Actionable suggestion:** Improve structured-output examples and errors for empty arrays, enums, escaped strings, and scene-specific evidence constraints.

## 08. Invalid or duplicate event IDs

- **Task:** Assemble a recap queue from supplied candidates.
- **Steps taken:** Validated Nova’s returned event selections.
- **Expected vs. actual result:** Expected: unique, exact candidate IDs. Actual: unavailable or duplicate IDs were returned, including e001_00 becoming e00_00.
- **Severity rating:** High.
- **Workaround used:** Validated exact IDs and duplicates, used bounded correction, and moved prepared-source selection to local ranking.
- **Actionable suggestion:** Provide constrained-selection examples that preserve opaque IDs and leading zeros.

## 09. Incomplete silent-scene and character coverage

- **Task:** Include important silent action and supplied characters.
- **Steps taken:** Reviewed scene classifications and an initial map containing only two characters.
- **Expected vs. actual result:** Expected: useful visual coverage and the supplied cast. Actual: dialogue-driven extraction omitted characters and left substantial footage unassessed.
- **Severity rating:** Medium.
- **Workaround used:** Combined sampled frames with optional transcripts, saved visual action annotations, and built maps from supplied SQLite character and relationship records.
- **Actionable suggestion:** Provide a video-understanding example covering silent scenes, optional transcripts, cast metadata, and sampling limitations.

## 10. Clips lacked sufficient context

- **Task:** Create short videos that help viewers recall events.
- **Steps taken:** Reviewed narrow event cuts and approximately one-second segments.
- **Expected vs. actual result:** Expected: concise clips with understandable setup and outcome. Actual: some cuts omitted necessary context.
- **Severity rating:** Medium.
- **Workaround used:** Enforced 7–45-second edits with up to four supported source ranges and excluded filler.
- **Actionable suggestion:** Provide recap-editing examples that preserve event context rather than cutting only around individual subtitle cues.

## 11. TV clip buffering

- **Task:** Play generated recap clips smoothly on Fire TV.
- **Steps taken:** Revised the sequential MP4 loading path after buffering concerns.
- **Expected vs. actual result:** Expected: reliable clip starts. Actual: buffering prompted a loading-strategy change; no measured before/after result was recorded.
- **Severity rating:** High.
- **Workaround used:** Implemented full-clip downloading, bounded caching, preloading of the next two clips, and lighter H.264/AAC encoding.
- **Actionable suggestion:** Provide a Fire OS WebView playlist example with codec, buffering, preload, memory, and decode diagnostics.

## 12. Saved progress did not enable recaps

- **Task:** Resume a partially watched title and open its recap.
- **Steps taken:** Reopened a cartoon with an older saved position.
- **Expected vs. actual result:** Expected: existing progress remains usable. Actual: missing interval records and sparse playback callbacks could trigger an additional-watch requirement.
- **Severity rating:** High.
- **Workaround used:** Added legacy progress recovery and played-TimeRanges tracking; later adopted playback-position-based recap eligibility.
- **Actionable suggestion:** Provide player examples covering resume persistence, sparse updates, seek handling, and progress migrations.

## 13. Recaps unavailable after seeking

- **Task:** Recap earlier story after seeking forward.
- **Steps taken:** Played briefly, sought to 5:00, and opened the recap or assistant.
- **Expected vs. actual result:** Expected: supported story before 5:00 is available. Actual: actual-watch-time requirements excluded skipped earlier scenes.
- **Severity rating:** High.
- **Workaround used:** Used the current playback position as the story cutoff, tracked exposure separately, and rebuilt queues when the cutoff changed.
- **Actionable suggestion:** Demonstrate separate handling of playback position, viewing exposure, recap eligibility, and spoiler boundaries.

## 14. Recap selection favored particular periods

- **Task:** Cover the available story fairly.
- **Steps taken:** Opened recaps near later timestamps and inspected selected moments.
- **Expected vs. actual result:** Expected: equal consideration across eligible periods and chronological playback. Actual: selection favored particular periods.
- **Severity rating:** Medium.
- **Workaround used:** Distributed selection opportunities across time buckets and used memory feedback to shift attention.
- **Actionable suggestion:** Provide recommendation examples and tests separating temporal coverage, relevance ranking, and playback order.

## 15. Related context arrived too late

- **Task:** Explain a forgotten event immediately.
- **Steps taken:** Requested more context while other clips were queued.
- **Expected vs. actual result:** Expected: related earlier setup plays next. Actual: unrelated clips could play before the requested context.
- **Severity rating:** Medium.
- **Workaround used:** Inserted requested context into a priority queue, preserved normal FIFO order, removed duplicates, and waited for context when necessary.
- **Actionable suggestion:** Provide asynchronous queue examples covering priority insertion, late responses, cancellation, and preserved playback.

## 16. Incorrect memory-feedback timing

- **Task:** Interpret remote actions and active viewing time consistently.
- **Steps taken:** Tested clip dwell, Down/Up navigation, pauses, buffering, and overlays.
- **Expected vs. actual result:** Expected: seven active seconds requests context once. Actual: the requested dwell behavior and overlapping feedback events required additional handling.
- **Severity rating:** Medium.
- **Workaround used:** Counted active playback only; added one-time dwell signals, early-skip handling, persistent feedback, and protection against later signals overwriting Not remember.
- **Actionable suggestion:** Provide interaction-timing examples with exposure clocks, overlay lifecycle handling, and race-condition tests.

## 17. Fullscreen surface ordering

- **Task:** Open the movie visibly in fullscreen.
- **Steps taken:** Started fullscreen while opening the player dialog.
- **Expected vs. actual result:** Expected: video is the visible top surface. Actual: a browser top-layer race could leave Home covering the playing movie.
- **Severity rating:** Medium.
- **Workaround used:** Completed the fullscreen transition before opening the player dialog.
- **Actionable suggestion:** Provide WebView examples covering fullscreen, HTML dialogs, focus restoration, and native immersive mode.

## 18. Invalid media downloads

- **Task:** Prepare complete demo videos and episode cuts.
- **Steps taken:** Downloaded the original media URLs and checked file content and duration.
- **Expected vs. actual result:** Expected: complete video files. Actual: some URLs returned unavailable HTML pages with HTTP 200, and one cached download had a missing tail.
- **Severity rating:** High.
- **Workaround used:** Rejected HTML, used documented mirrors, repaired incomplete downloads, and validated hashes, codecs, durations, and cut boundaries.
- **Actionable suggestion:** Include content validation, resumable downloads, and actionable media errors in preparation examples.

