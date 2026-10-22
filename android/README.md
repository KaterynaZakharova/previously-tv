# Fire OS wrapper

Open this directory in Android Studio. See the root README, section 3, for the
complete laptop → Android Studio → ADB → physical TV steps.

The official Gradle 8.9 wrapper is included. Use JDK 17, SDK Platform 35 and
Platform-Tools. `gradlew.bat assembleDebug` on Windows; `./gradlew assembleDebug`
on Linux/macOS. APK output: `app/build/outputs/apk/debug/app-debug.apk`.

The wrapper loads `http://LAPTOP_LAN_IP:8000/?tv=1`; it does not run Python or
FFmpeg on the TV. The laptop stays awake on the same LAN. Menu on Home changes the URL.
Rebuild/reinstall this version to enable the native Menu and Media Previous routing.
Home/details: D-pad navigates and OK selects. Full playback fills the TV, with
Previous, Play/Pause, Previously and far-right Next. Down focuses controls;
Left/Right moves among them; Up returns to playback. Select Previously to pause
and save the source and open the feed.

Feed: Down advances and marks Remember after two seconds of playback; an earlier
Down skips. Up revisits and requests context. Left opens scene notes, Right opens
the database map at the current clip's original-source timestamp. Menu opens AI
queue/model/scene details. Center/Play-Pause toggles; Back closes a popup or returns
to the original video at its unchanged position. Media Previous/Next work too.

Laptop equivalents: arrows, Enter/Space, M/ContextMenu/F10 for Menu, and
Backspace/Escape for Back. See the root README section 2.

Fire OS only. This Android APK is not a Vega OS build. LAN HTTP is enabled for
development. The root VALIDATION.md states whether compilation/device tests were
possible in the delivery environment.

## Voice chat (version 2.1)

Rebuild/reinstall the APK for the new native speech bridge and microphone permission.
Hold OK for 0.7 seconds to open chat; a short OK activates on release. The standard
Alexa microphone button remains owned by the Fire TV system. See
[the voice guide](../docs/VOICE_CHAT.md) for supported device speech, an optional
app-accessible microphone + AWS Transcribe path, Polly output and test steps.
