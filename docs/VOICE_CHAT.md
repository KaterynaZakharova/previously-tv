# Voice chatbot for watching and recaps

Open **Ask Previously** while playing a movie/episode or browsing the recap feed. The app pauses the video, opens a glass conversation modal, obtains a short greeting from Nova and reads it aloud. When speech output finishes, the microphone listens for a question. Nova returns a text answer with source references before the playback position, the app speaks it, then listens for the next question. Choose **Finish & return**, press Back/Escape, or ask to finish to leave chat. The original player/recap resumes at the same position if it was playing when chat opened; a previously paused video stays paused.

Opening the modal stops recap exposure/dwell timers, so time talking to the assistant never becomes a seven-second “not remember” signal. Late network/model/speech callbacks after closing are ignored. The question field remains available when speech capture or output is unavailable.

## Remote and laptop controls

| Input | Action |
|---|---|
| Player/recap microphone button | Open chat; focus/select with the remote where reachable |
| Hold OK for 0.7 seconds | Open chat on the updated Android wrapper; short OK still activates normally on release |
| Voice/Search/Assist key, **if delivered to the app** | Open chat |
| Laptop **V** | Open chat, or start/stop the next question while chat is open |
| D-pad arrows | Move among chat controls and the question field |
| OK / Enter | Select a control; Enter in the question field sends the question |
| Ask a question | Start listening; select again to finish recording |
| Hear again | Replay the latest answer |
| Back / Escape / Finish & return | Close chat and restore playback |

**Standard Fire TV Alexa Voice Remote limitation:** Amazon routes its microphone button to Alexa/global voice search, regardless of the foreground app. A normal third-party app cannot reliably capture that key or the remote microphone audio. Mapping `KEYCODE_VOICE_ASSIST` does not override this system behavior. Use **hold OK** or the on-screen mic to open the app conversation. Speech input then requires an app-accessible microphone and a supported recognition path. This release does not register an Alexa skill or claim to intercept Alexa.

Official explanation: [Amazon Fire TV search and voice behavior](https://developer.amazon.com/docs/fire-tv/implementing-search.html). USB/Bluetooth microphone support and audio routing vary by Fire TV model; simply pairing a voice remote does not make its mic available to the app.

## Local mode: no AWS account

Keep the existing Nova developer API configuration. The chatbot needs a Nova key even when the movie already has a prepared logbook, because answering questions is a new AI task:

```dotenv
PREVIOUSLY_RUN_MODE=local
NOVA_API_KEY=YOUR_EXISTING_KEY
NOVA_MODEL=auto
PREVIOUSLY_VOICE_OUTPUT=device
PREVIOUSLY_VOICE_LANGUAGE=en-US
```

Run `python run.py --mode local --host 0.0.0.0 --port 8000`. On a laptop, open `http://localhost:8000/?tv=1` in a browser supporting Web Speech, such as desktop Chrome. Start a film, press V and allow microphone access. Speech recognition may use the browser vendor's online service; this is not an offline speech model. Secure-context/permission restrictions apply, especially when opening a LAN IP from another device. Use localhost for laptop testing or HTTPS where the browser requires it.

Android uses `SpeechRecognizer` when the device exposes a recognition service, and `TextToSpeech` for device output. Fire OS may lack either service. The app checks availability and displays a specific error; no simulated recognition or fake Nova answer is substituted. Type a question if input is unavailable. An unavailable device TTS engine leaves the text answer readable; AWS Polly is an optional spoken-output alternative.

## AWS mode: Nova + Polly, optional Transcribe

Install the existing `requirements-aws.txt`, configure the AWS profile/region and Nova Bedrock access described in [RUN_MODES.md](RUN_MODES.md). Both direct Bedrock, Strands and AgentCore Nova transports support the chatbot through the same `make_ai` interface.

```dotenv
PREVIOUSLY_RUN_MODE=aws
AWS_AI_ENGINE=strands
AWS_REGION=us-east-1
AWS_PROFILE=previously
AWS_BEDROCK_MODEL_ID=amazon.nova-pro-v1:0
PREVIOUSLY_VOICE_OUTPUT=polly
AWS_POLLY_VOICE_ID=Joanna
AWS_POLLY_ENGINE=neural
PREVIOUSLY_VOICE_LANGUAGE=en-US
# Only needed for raw microphone input when Android recognition is absent:
AWS_TRANSCRIBE_BUCKET=YOUR_PRIVATE_BUCKET_IN_THE_SAME_REGION
AWS_TRANSCRIBE_PREFIX=previously/voice
```

Polly speaks **Nova's validated text answer**. This implementation uses turn-based speech recognition + Nova text reasoning + speech synthesis; it does not claim to use Nova Sonic's streaming speech-to-speech API. In local mode, device/browser speech avoids AWS speech dependencies. Set `PREVIOUSLY_VOICE_OUTPUT=device` in AWS mode too if the device's own TTS is preferred.

When Android has no recognition service and `AWS_TRANSCRIBE_BUCKET` is configured, the wrapper records from its app-accessible microphone with `AudioRecord`: mono 16-bit PCM WAV at 16 kHz, at most 20 seconds, stopping after speech followed by roughly two seconds of silence. The Python server submits it to Amazon Transcribe through a private S3 prefix and uses the transcript as the next question. Recording a quiet input/no microphone produces an error rather than an invented question. Batch Transcribe adds latency; it is not a realtime voice stream. The UI shows recognition/thinking/speaking state throughout.

The Transcribe bucket must be in the selected service region. Configure a one-day expiration lifecycle rule on the voice prefix as a backstop. The server deletes the input, output and transcription-job metadata after completion when possible. A timed-out/running job can outlive a cleanup attempt, so bucket lifecycle and job-retention policy still matter. Do not store source movies or viewer state in this temporary audio prefix.

## Required AWS permissions

These are additions to the selected Nova transport permissions in [AWS_IAM.md](AWS_IAM.md):

- `polly:SynthesizeSpeech` for the server identity, when Polly is selected. Polly synthesis uses `Resource: "*"`; scope the identity/account and other policy conditions as needed.
- `transcribe:StartTranscriptionJob`, `transcribe:GetTranscriptionJob`, and `transcribe:DeleteTranscriptionJob` for the `previously-voice-*` jobs.
- `s3:PutObject`, `s3:GetObject`, and `s3:DeleteObject` for the chosen private voice prefix. Ensure Transcribe can access that input/output location according to AWS's service setup. SSE-S3 is requested for uploaded recordings.

Speech calls are made by the laptop server's identity. With AgentCore inference, **speech permissions still belong to the laptop identity**, not only the AgentCore role. The TV receives no AWS or Nova credential. No bucket, role or service is automatically provisioned.

Official APIs: [Transcribe batch jobs](https://docs.aws.amazon.com/boto3/latest/reference/services/transcribe/client/start_transcription_job.html), [Polly synthesis](https://docs.aws.amazon.com/boto3/latest/reference/services/polly/client/synthesize_speech.html), [Android SpeechRecognizer](https://developer.android.com/reference/android/speech/SpeechRecognizer), [Android TextToSpeech](https://developer.android.com/reference/android/speech/tts/TextToSpeech).

## Spoilers, grounding and retention

The backend builds a frozen context at the current original-source timestamp. It includes every supported scene before that boundary, including seek gaps and earlier episodes of the same title, and excludes later scenes, later episodes and untimed character bios. Seeking to 5:00 makes the first five minutes available even after only two seconds of actual playback. Rewinding lowers the cutoff. Recap source time follows the current edit/montage back to its original source position. Relationship/name evidence comes from the existing database story map. Dialogue and saved action logbooks are evidence; the question itself does not authorize revealing content beyond the playback position.

Retrieval uses question terms, current-scene proximity and prior cited evidence, with bounded context and conversation history. Nova must cite supplied evidence for factual answers or explicitly report insufficient evidence. Evidence/time boundaries are enforced in code; these constraints do not guarantee every future model interpretation is factually perfect. Responses with invalid structure/citations are repaired or rejected. The chatbot never calls AI to rebuild the character graph or reanalyzes the full film merely to answer a question.

Chat sessions are held in server memory, owned by the viewer cookie, with at most eight retained Q&A turns and fourteen speech replies. They expire after thirty minutes of inactivity (purged on subsequent activity) and are deleted on explicit Finish. Questions and model responses still enter the existing `runtime/logs/ai-responses.jsonl` audit log, as required by the project. Transcription logs contain recognized text, not raw audio; raw audio is not written to the laptop's disk. Restarting the server clears active conversations without clearing watch progress.

## Apply, build and test

1. Extract `previously_voice_update.zip` over the project root. It does not replace movies, `.env`, SQLite or viewer state. Keep the optional speech settings above in your existing `.env`.
2. Restart Python. Test the laptop UI with V or the on-screen mic. Try a follow-up, Hear again, typing, Finish, an unavailable key and an unwatched-plot question.
3. **Rebuild and reinstall the Android APK.** This change adds the native voice bridge, `RECORD_AUDIO`, speech-service manifest queries, hold-OK handling and version 2.1. Open the `android` folder in Android Studio, use JDK 17, then Build → Generate Bundle(s) / APK(s) → Generate APK(s). Output: `android/app/build/outputs/apk/debug/app-debug.apk`.
4. Install with Android Studio/ADB as before. Use the laptop LAN URL, allow the microphone permission when asked, and test a microphone actually available to apps. If a permission dialog interrupts initial listening, select Ask a question again after granting it.
5. Test opening chat in both original playback and recaps. Leave it open longer than seven seconds and confirm no new recap memory signal. Finish and confirm the same source/clip position resumes. Verify that an already paused video remains paused.

Automated checks:

```powershell
python -m unittest discover -s tests -v
node tests/voice_ui_smoke.cjs
node tests/home_ui_smoke.cjs
node tests/ui_smoke.cjs
node tests/feed_controller.cjs
node tests/feed_ui_smoke.cjs
```

Tests use controlled model/speech-service responses, not paid API calls. The Kotlin source/manifest are supplied, but Android compilation could not be completed in the authoring environment because the Gradle distribution download was network-blocked; no built/physically tested APK is claimed. Live Nova/Polly/Transcribe and real-device microphone routing still require your account/device verification.
