package com.previously.tv

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.net.Uri
import android.webkit.WebResourceRequest
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText

class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var voice: VoiceBridge
    private var serverOrigin: String? = null
    private val keyHandler = Handler(Looper.getMainLooper())
    private var centerPending = false
    private var centerLong = false
    private val centerVoice = Runnable {
        if (centerPending) {
            centerLong = true
            js("window.PreviouslyTV && window.PreviouslyTV.voice()")
        }
    }
    private val preferences by lazy { getSharedPreferences("previously", MODE_PRIVATE) }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.mediaPlaybackRequiresUserGesture = false
        web.settings.useWideViewPort = true
        web.settings.loadWithOverviewMode = true
        voice = VoiceBridge(this, { trustedPage() }) { event ->
            js("window.VoiceChat && window.VoiceChat.nativeEvent(" + event.toString() + ")")
        }
        web.addJavascriptInterface(voice, "PreviouslyVoice")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                return !sameOrigin(request.url)
            }
        }
        web.webChromeClient = WebChromeClient()
        setContentView(web)
        val url = preferences.getString("serverUrl", null)
        if (url == null) configureServer() else loadServer(url)
    }
    private fun sameOrigin(uri: Uri): Boolean {
        val origin = serverOrigin?.let { Uri.parse(it) } ?: return false
        return uri.scheme == origin.scheme && uri.host == origin.host && uri.port == origin.port
    }
    private fun trustedPage() = web.url?.let { sameOrigin(Uri.parse(it)) } == true
    private fun loadServer(url: String) {
        voice.stopOnMain()
        serverOrigin = url.trim().trimEnd('/')
        val target = url.trim().trimEnd('/') + "/?tv=1"
        web.loadUrl(target)
        web.requestFocus()
    }
    private fun configureServer() {
        val input = EditText(this)
        input.setSingleLine(true)
        input.hint = "http://192.168.1.10:8000"
        input.setText(preferences.getString("serverUrl", "http://"))
        AlertDialog.Builder(this).setTitle("Your laptop’s Python server")
            .setMessage("Enter its LAN URL. Press Menu on Home later to change it; in the recap feed Menu opens AI details.")
            .setView(input).setPositiveButton("Connect") { _, _ ->
                val url = input.text.toString().trim()
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    preferences.edit().putString("serverUrl", url).apply()
                    loadServer(url)
                } else configureServer()
            }.setNegativeButton("Cancel", null).show()
    }
    private fun js(code: String) { web.evaluateJavascript(code, null) }
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (voice.textEntryFocused && keyCode != KeyEvent.KEYCODE_BACK && keyCode != KeyEvent.KEYCODE_MENU)
            return super.onKeyDown(keyCode, event)
        val direction = when (keyCode) {
            KeyEvent.KEYCODE_DPAD_LEFT -> "left"
            KeyEvent.KEYCODE_DPAD_RIGHT -> "right"
            KeyEvent.KEYCODE_DPAD_UP -> "up"
            KeyEvent.KEYCODE_DPAD_DOWN -> "down"
            else -> null
        }
        if (direction != null) { if (event.repeatCount > 0) return true; js("window.PreviouslyTV && window.PreviouslyTV.navigate('$direction')"); return true }
        when (keyCode) {
            KeyEvent.KEYCODE_VOICE_ASSIST, KeyEvent.KEYCODE_ASSIST, KeyEvent.KEYCODE_SEARCH -> {
                if (event.repeatCount == 0) js("window.PreviouslyTV && window.PreviouslyTV.voice()")
                return true
            }
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> {
                if (event.repeatCount == 0) {
                    centerPending = true; centerLong = false
                    keyHandler.postDelayed(centerVoice, 700)
                }
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> { js("window.PreviouslyTV && window.PreviouslyTV.togglePlayback()"); return true }
            KeyEvent.KEYCODE_MEDIA_NEXT -> { js("window.PreviouslyTV && window.PreviouslyTV.next()"); return true }
            KeyEvent.KEYCODE_MEDIA_PREVIOUS -> { js("window.PreviouslyTV && window.PreviouslyTV.previous()"); return true }
            KeyEvent.KEYCODE_MENU -> {
                if (event.repeatCount == 0) web.evaluateJavascript("window.PreviouslyTV ? window.PreviouslyTV.menu() : false") { result -> if (result != "true") configureServer() }
                return true
            }
            KeyEvent.KEYCODE_BACK -> {
                web.evaluateJavascript("window.PreviouslyTV ? window.PreviouslyTV.back() : false") { result -> if (result != "true") finish() }
                return true
            }
        }
        return super.onKeyDown(keyCode, event)
    }
    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (centerPending && (keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER)) {
            keyHandler.removeCallbacks(centerVoice)
            centerPending = false
            if (!centerLong) js("window.PreviouslyTV && window.PreviouslyTV.activate()")
            return true
        }
        return super.onKeyUp(keyCode, event)
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == VoiceBridge.AUDIO_PERMISSION)
            voice.permissionResult(grantResults.firstOrNull() == android.content.pm.PackageManager.PERMISSION_GRANTED)
    }
    override fun onPause() { keyHandler.removeCallbacks(centerVoice); centerPending = false; voice.stopOnMain(); js("window.VoiceChat && window.VoiceChat.suspend()"); js("document.querySelectorAll('video').forEach(v=>v.pause())"); web.onPause(); super.onPause() }
    override fun onResume() { super.onResume(); if (::web.isInitialized) web.onResume() }
    override fun onDestroy() { voice.destroy(); web.removeJavascriptInterface("PreviouslyVoice"); web.destroy(); super.onDestroy() }
}
