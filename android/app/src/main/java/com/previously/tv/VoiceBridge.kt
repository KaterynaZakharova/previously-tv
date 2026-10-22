package com.previously.tv

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Base64
import android.webkit.JavascriptInterface
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import kotlin.math.sqrt

/** Only installed on the explicitly configured server's WebView. No AWS keys. */
class VoiceBridge(
    private val activity: Activity,
    private val trusted: () -> Boolean,
    private val callback: (JSONObject) -> Unit
) : RecognitionListener {
    companion object { const val AUDIO_PERMISSION = 401 }
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var ttsFailed = false
    private var pendingSpeech: Triple<String, String, String>? = null
    private var pendingListen: Triple<String, String, Boolean>? = null
    private var listenId: String? = null
    @Volatile var textEntryFocused = false
        private set
    @Volatile private var captureSerial = 0
    @Volatile private var recording = false
    @Volatile private var discardRecording = false
    private var destroyed = false

    init {
        tts = TextToSpeech(activity) { status ->
            main.post {
                ttsReady = status == TextToSpeech.SUCCESS
                ttsFailed = !ttsReady
                pendingSpeech?.let { (id, text, language) ->
                    pendingSpeech = null
                    speakOnMain(id, text, language)
                }
            }
        }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) { emit("speech_done", utteranceId) }
            @Deprecated("Legacy callback required by Android")
            override fun onError(utteranceId: String?) { emit("speech_error", utteranceId, "Device speech output is unavailable. Use AWS Polly or install a TTS voice.") }
        })
    }

    private fun emit(kind: String, id: String?, message: String? = null, extra: JSONObject? = null) {
        main.post {
            if (destroyed || !trusted() || id == null) return@post
            val event = extra ?: JSONObject()
            event.put("kind", kind).put("id", id)
            if (message != null) event.put("message", message)
            callback(event)
        }
    }
    private fun valid(id: String) = id.matches(Regex("voice-[0-9]+-[0-9]+"))

    @JavascriptInterface
    fun focusTextEntry(enabled: Boolean) { main.post { if (trusted()) textEntryFocused = enabled } }

    @JavascriptInterface
    fun speak(id: String, text: String, language: String) {
        main.post { if (trusted() && valid(id) && text.length <= 2400) speakOnMain(id, text, language) }
    }
    private fun speakOnMain(id: String, text: String, language: String) {
        if (!ttsReady) {
            if (ttsFailed) emit("speech_error", id, "Device speech output is unavailable. Configure AWS Polly for spoken replies.")
            else {
                pendingSpeech = Triple(id, text, language)
                main.postDelayed({
                    if (pendingSpeech?.first == id) {
                        pendingSpeech = null
                        emit("speech_error", id, "Device speech output did not initialize.")
                    }
                }, 6000)
            }
            return
        }
        val result = tts?.setLanguage(Locale.forLanguageTag(language))
        if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
            emit("speech_error", id, "The selected speech language is unavailable on this device.")
            return
        }
        if (tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.ERROR)
            emit("speech_error", id, "Device speech output failed.")
    }

    @JavascriptInterface
    fun listen(id: String, language: String, cloudAvailable: Boolean) {
        main.post {
            if (!trusted() || !valid(id) || destroyed) return@post
            stopOnMain()
            if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                pendingListen = Triple(id, language, cloudAvailable)
                activity.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), AUDIO_PERMISSION)
                return@post
            }
            listenOnMain(id, language, cloudAvailable)
        }
    }
    fun permissionResult(granted: Boolean) {
        val pending = pendingListen ?: return
        pendingListen = null
        if (granted && trusted()) listenOnMain(pending.first, pending.second, pending.third)
        else emit("error", pending.first, "Microphone permission was denied. Allow it in app settings, or type your question.")
    }
    private fun listenOnMain(id: String, language: String, cloudAvailable: Boolean) {
        listenId = id
        if (SpeechRecognizer.isRecognitionAvailable(activity)) {
            recognizer?.destroy()
            recognizer = SpeechRecognizer.createSpeechRecognizer(activity).also { it.setRecognitionListener(listener(id)) }
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            try { recognizer?.startListening(intent) }
            catch (_: Exception) { emit("error", id, "Device speech recognition could not start. Try typing a question.") }
        } else if (cloudAvailable) recordWav(id)
        else emit("error", id, "No app speech service is available. The Alexa remote mic belongs to Alexa. Use a supported input microphone with AWS Transcribe, or type your question.")
    }

    @Suppress("MissingPermission")
    private fun recordWav(id: String) {
        if (recording) { emit("error", id, "The previous microphone recording is still stopping. Try again."); return }
        val serial = ++captureSerial
        recording = true
        discardRecording = false
        Thread {
            var recorder: AudioRecord? = null
            try {
                val bufferSize = maxOf(4096, AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT))
                recorder = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize)
                if (recorder.state != AudioRecord.STATE_INITIALIZED) throw IllegalStateException("No input microphone")
                val pcm = ByteArrayOutputStream()
                val samples = ShortArray(1600)
                recorder.startRecording()
                if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING) throw IllegalStateException("Microphone did not start")
                var frames = 0
                var lastVoiceFrame = 0
                var heardVoice = false
                while (recording && serial == captureSerial && frames < 320000) {
                    val count = recorder.read(samples, 0, minOf(samples.size, 320000 - frames))
                    if (count <= 0) throw IllegalStateException("Microphone input interrupted")
                    val rms = sqrt((0 until count).sumOf { samples[it].toDouble() * samples[it] } / count)
                    if (rms > 600) { heardVoice = true; lastVoiceFrame = frames }
                    val bytes = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN)
                    for (i in 0 until count) bytes.putShort(samples[i])
                    pcm.write(bytes.array()); frames += count
                    if (heardVoice && frames - lastVoiceFrame > 32000) break
                    if (!heardVoice && frames > 128000) break
                }
                if (!discardRecording && serial == captureSerial) {
                    if (!heardVoice || frames < 3200) emit("error", id, "No speech heard. Check that an app-accessible microphone is connected.")
                    else {
                        val bytes = pcm.toByteArray()
                        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
                        header.put("RIFF".toByteArray()).putInt(36 + bytes.size).put("WAVEfmt ".toByteArray())
                        header.putInt(16).putShort(1.toShort()).putShort(1.toShort()).putInt(16000).putInt(32000)
                            .putShort(2.toShort()).putShort(16.toShort()).put("data".toByteArray()).putInt(bytes.size)
                        // Keep each WebView IPC message well below Android's binder limit.
                        val parts = Base64.encodeToString(header.array() + bytes, Base64.NO_WRAP).chunked(32000)
                        parts.forEachIndexed { index, part ->
                            emit("audio_chunk", id, extra = JSONObject().put("index", index)
                                .put("total", parts.size).put("data", part))
                        }
                    }
                }
            } catch (_: Exception) {
                if (!discardRecording && serial == captureSerial) emit("error", id, "Microphone capture is unavailable. Connect a supported USB/Bluetooth input device, or type your question.")
            } finally {
                try { recorder?.stop() } catch (_: Exception) {}
                recorder?.release()
                if (serial == captureSerial) recording = false
            }
        }.start()
    }

    @JavascriptInterface
    fun stopListening() { main.post { if (recording) recording = false else recognizer?.stopListening() } }
    @JavascriptInterface
    fun cancel() { main.post { stopOnMain() } }
    fun stopOnMain() {
        pendingSpeech = null; pendingListen = null; listenId = null
        captureSerial++; discardRecording = true; recording = false
        recognizer?.cancel()
        tts?.stop()
    }
    fun destroy() { stopOnMain(); destroyed = true; recognizer?.destroy(); tts?.shutdown() }
    private fun listener(id: String) = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() { if (listenId == id) this@VoiceBridge.onEndOfSpeech() }
        override fun onError(error: Int) { if (listenId == id) this@VoiceBridge.onError(error) }
        override fun onResults(results: Bundle?) { if (listenId == id) this@VoiceBridge.onResults(results) }
        override fun onPartialResults(partialResults: Bundle?) {}
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }
    override fun onReadyForSpeech(params: Bundle?) {}
    override fun onBeginningOfSpeech() {}
    override fun onRmsChanged(rmsdB: Float) {}
    override fun onBufferReceived(buffer: ByteArray?) {}
    override fun onEndOfSpeech() { emit("processing", listenId) }
    override fun onError(error: Int) {
        emit("error", listenId, when(error) {
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission is unavailable."
            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech heard. Ask again, or type your question."
            else -> "Device speech recognition is unavailable (code $error). Try again, or type your question."
        })
    }
    override fun onResults(results: Bundle?) {
        val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
        if (text.isNullOrBlank()) emit("error", listenId, "No speech heard. Try again.")
        else emit("transcript", listenId, extra = JSONObject().put("text", text))
    }
    override fun onPartialResults(partialResults: Bundle?) {}
    override fun onEvent(eventType: Int, params: Bundle?) {}
}
