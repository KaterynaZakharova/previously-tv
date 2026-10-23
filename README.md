# Previously TV

## License

**Project code and custom UI are licensed under the [MIT License](LICENSE).**  
Copyright © 2026 Previously TV contributors.

Third-party movies, transcripts, images, fonts, icons, and the Gradle wrapper retain their own licenses. Preserve media attribution and modification notices.

| Demo film | License | Original project |
|---|---|---|
| Tears of Steel | CC BY 3.0 | [Blender Foundation](https://mango.blender.org/) |
| Spring | CC BY 4.0 | [Blender Studio](https://studio.blender.org/projects/spring/) |
| Sprite Fright | CC BY 1.0 | [Blender Studio](https://studio.blender.org/projects/sprite-fright/) |

## About

**AI recaps for Fire TV that adapt to your memory.**

Previously TV helps viewers quickly recall a movie or series through short clips from the original footage. Open the recap feed, move past familiar moments, and request earlier context for events you have forgotten.

Built for the Fire TV track of the Amazon Developer Hackathon, the app runs in a Kotlin WebView wrapper on **Fire OS**.

## Features

- Personalized 7–45-second video recaps.
- Remember / Not remember feedback through remote navigation and active viewing time.
- Related earlier context inserted immediately after a forgotten moment.
- Character and relationship maps with relevant clip selection.
- Nova-powered questions and answers about the story so far.
- Recap and assistant evidence bounded by the relevant playback timestamp.
- Persistent progress, memory feedback, and queues.
- Local hosting with either the Nova developer API or optional AWS inference.

## How it works

1. **Analyze once:** Nova processes timestamped frames and available dialogue, saving action annotations and proposed edits in SQLite.
2. **Personalize:** selects supported clips using the playback cutoff, memory feedback, and character or relationship filters.
3. **Render and play:** cuts the source footage; the frontend caches clips and preloads upcoming videos.
4. **Adapt:** forgotten moments request earlier context, then the remaining queue resumes.

Prepared-source selection does not require another model call after each feedback signal. Shared source annotations and individual viewer state are stored separately.

The **Python server, databases, video files, and FFmpeg run on the computer**. The TV connects over the local network. AWS mode changes inference and enables optional services; it does not automatically deploy the entire application to AWS.

## Project structure

| Path | Purpose |
|---|---|
| `run.py` | Local/AWS launcher |
| `server.py` | API, frontend, and media serving |
| `backend/` | Analysis, selection, story map, chat, and speech |
| `frontend/` | HTML/CSS/JavaScript interface |
| `android/` | Kotlin Fire OS wrapper and Gradle project |
| `assets/` | Required catalog, media, transcripts, and images |
| `tools/` | Preparation, validation, and AWS utilities |
| `integrations/aws/` | AgentCore and SageMaker entry points |
| `runtime/` | Generated clips, caches, viewer state, and logs |
| `tests/` | Python and JavaScript checks |
| `docs/` | Detailed integration guides |


## Controls

| Action | Fire TV | Desktop |
|---|---|---|
| Navigate / select on Home | D-pad / OK | Arrows / Enter |
| Play / pause | OK or Play/Pause | Enter or Space |
| Next recap | Down | Down |
| Revisit and request context | Up | Up |
| Scene notes in recap | Left | Left |
| Story map in recap | Right | Right |
| AI/queue details in recap | Menu | M |
| Open assistant | On-screen microphone or hold OK | Microphone or V |
| Close / return to source | Back | Escape or Backspace |
| Change server URL | Menu on Home | TV wrapper only |

Down before two seconds skips without rating. Down from two to under seven seconds signals Remember. Seven seconds of active playback signals Not remember once per visit; pauses, buffering, overlays, and conversations do not count.

The Alexa remote microphone belongs to Alexa. App voice input needs a supported microphone path; a phone microphone worked in the demo. Typed questions remain available.


## Installation and run

Run from the repository root in an activated Python environment, with FFmpeg/FFprobe and the prepared `assets/` available. Local live AI needs `NOVA_API_KEY` in `.env`; AWS needs configured credentials and a region/model accessible to your account.

### Build the APK

Install Android Studio, Android SDK Platform 35, Platform-Tools, and **JDK 17**. Open `android/` in Android Studio, set **Settings → Build, Execution, Deployment → Build Tools → Gradle → Gradle JDK** to Java 17, and let Gradle sync.

Select the `debug` build variant and choose **Build → Generate Bundle(s) / APK(s) → Generate APK(s)**.

APK output: `android/app/build/outputs/apk/debug/app-debug.apk`. An emulator is not required.

### To test locally, run
```bash
python3 run.py --mode local --host 0.0.0.0 --port 8000
```
### To test using AWS, install and run
Use Python 3.10+; Python 3.12 is recommended.
```bash
python3 -m pip install -r requirements.txt
python3 run.py --mode aws --aws-engine bedrock --host 0.0.0.0 --port 8000
```
Open **http://localhost:8000/?tv=1**. See [AWS configuration](docs/RUN_MODES.md) for account setup.

### Install and start on Fire TV

Keep the Python server running using either command above. Put the computer and TV on the same network, enable **ADB debugging** on the TV, and allow the server through the computer’s private-network firewall.

Run these commands from the **parent directory containing `previously-tv/`**. Replace `DEVICE_IP:DEVICE_PORT` with the TV’s ADB address and accept its debugging authorization prompt.

**Windows (PowerShell)**

```powershell
$adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
& $adb connect DEVICE_IP:DEVICE_PORT
& $adb install -r previously-tv\android\app\build\outputs\apk\debug\app-debug.apk
& $adb shell am start -n com.previously.tv/.MainActivity
```

**Linux**

```bash
adb connect DEVICE_IP:DEVICE_PORT
adb install -r previously-tv/android/app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.previously.tv/.MainActivity
```

If running from the repository root, use `android/app/build/outputs/apk/debug/app-debug.apk` as the installation path instead.

On first launch, enter `http://LAPTOP_LAN_IP:8000` in the TV app, using the **computer’s IP**, not the TV’s IP or `localhost`. The wrapper adds `/?tv=1` automatically. Keep the computer awake and the server running; **Menu on Home** changes the saved URL.
