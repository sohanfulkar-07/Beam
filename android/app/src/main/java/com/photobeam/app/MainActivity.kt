package com.photobeam.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.photobeam.app.ui.screens.HomeScreen
import com.photobeam.app.ui.screens.ReceiveScreen
import com.photobeam.app.ui.screens.SendScreen
import com.photobeam.app.ui.screens.HistoryScreen
import com.photobeam.app.ui.screens.MirrorScreen
import com.photobeam.app.ui.screens.PairScreen
import com.photobeam.app.ui.theme.PhotoBeamTheme

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import android.util.Log

class MainActivity : ComponentActivity() {

    // URI from Share Sheet or QR deep link, passed to SendScreen
    private var sharedUris by mutableStateOf<List<Uri>>(emptyList())
    private var qrUri by mutableStateOf<String?>(null)
    private var initialScreen by mutableStateOf<String?>(null)
    private var autoAccept by mutableStateOf(false)

    companion object {
        private var wakeLock: android.os.PowerManager.WakeLock? = null

        fun setKeepScreenOn(activity: ComponentActivity?, keepOn: Boolean) {
            activity?.runOnUiThread {
                if (keepOn) {
                    activity.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                } else {
                    activity.window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                }
            }
        }

        fun acquireWakeLock(context: android.content.Context) {
            try {
                if (wakeLock == null) {
                    val pm = context.getSystemService(android.content.Context.POWER_SERVICE) as android.os.PowerManager
                    wakeLock = pm.newWakeLock(android.os.PowerManager.PARTIAL_WAKE_LOCK, "PhotoBeam:TransferWakeLock").apply {
                        setReferenceCounted(false)
                    }
                }
                wakeLock?.acquire(15 * 60 * 1000L) // 15 min max safety timeout
                android.util.Log.d("PhotoBeam", "[DIAG] Transfer WakeLock acquired")
            } catch (e: Exception) {
                android.util.Log.w("PhotoBeam", "Failed to acquire WakeLock: ${e.message}")
            }
        }

        fun releaseWakeLock() {
            try {
                if (wakeLock?.isHeld == true) {
                    wakeLock?.release()
                    android.util.Log.d("PhotoBeam", "[DIAG] Transfer WakeLock released")
                }
            } catch (e: Exception) {
                android.util.Log.w("PhotoBeam", "Failed to release WakeLock: ${e.message}")
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        releaseWakeLock()
        setKeepScreenOn(this, false)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        handleIntent(intent)

        setContent {
            PhotoBeamTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    PhotoBeamApp(
                        initialSharedUris = sharedUris,
                        initialQrUri = qrUri,
                        initialScreen = initialScreen,
                        autoAccept = autoAccept,
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.data?.scheme == "photobeam") {
            qrUri = intent.data.toString()
        }
        intent?.getStringExtra("qr_uri")?.let {
            qrUri = it
        }
        intent?.getStringArrayExtra("stream_uris")?.let { arr ->
            sharedUris = arr.map { Uri.parse(it) }
        }
        intent?.getStringExtra("stream_uri")?.let { s ->
            sharedUris = listOf(Uri.parse(s))
        }
        when (intent?.action) {
            Intent.ACTION_SEND -> {
                val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
                if (uri != null) sharedUris = listOf(uri)
            }
            Intent.ACTION_SEND_MULTIPLE -> {
                @Suppress("UNCHECKED_CAST")
                val uris = intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)
                if (!uris.isNullOrEmpty()) sharedUris = uris
            }
        }
        intent?.getStringExtra("screen")?.let {
            initialScreen = it
        }
        if (intent?.hasExtra("auto_accept") == true) {
            autoAccept = intent.getBooleanExtra("auto_accept", false)
        }
        Log.d("PhotoBeam", "[INTENT] Handled intent: screen=$initialScreen, qrUri=$qrUri, sharedUris=$sharedUris, autoAccept=$autoAccept")
    }
}

@Composable
fun PhotoBeamApp(
    initialSharedUris: List<Uri> = emptyList(),
    initialQrUri: String? = null,
    initialScreen: String? = null,
    autoAccept: Boolean = false,
) {
    val navController = rememberNavController()

    // Mutable so the HomeScreen can inject URIs picked from the file picker
    var activeSharedUris by remember(initialSharedUris) { mutableStateOf(initialSharedUris) }

    LaunchedEffect(initialScreen, initialQrUri) {
        if (initialScreen != null) {
            navController.navigate(initialScreen) {
                launchSingleTop = true
            }
        } else if (initialQrUri != null) {
            if (initialQrUri.startsWith("photobeam://pair/")) {
                navController.navigate("pair") {
                    launchSingleTop = true
                }
            } else {
                navController.navigate("send") {
                    launchSingleTop = true
                }
            }
        } else if (initialSharedUris.isNotEmpty()) {
            navController.navigate("send") {
                launchSingleTop = true
            }
        }
    }

    fun navigateBackOrHome() {
        if (!navController.popBackStack()) {
            navController.navigate("home") {
                popUpTo(0)
            }
        }
    }

    NavHost(navController = navController, startDestination = "home") {
        composable("home") {
            HomeScreen(
                onNavigateReceive = { navController.navigate("receive") },
                onNavigateSend = { navController.navigate("send") },
                onNavigateSendWithFiles = { uris ->
                    // Inject picked URIs then navigate to SendScreen immediately
                    activeSharedUris = uris
                    navController.navigate("send") { launchSingleTop = true }
                },
                onNavigatePair = { navController.navigate("pair") },
                onNavigateHistory = { navController.navigate("history") },
                onNavigateMirror = { deviceId ->
                    navController.navigate("mirror/$deviceId") {
                        launchSingleTop = true
                    }
                }
            )
        }
        composable("pair") {
            PairScreen(
                onBack = { navigateBackOrHome() },
                onPairingComplete = { navigateBackOrHome() },
                initialQrUri = initialQrUri,
            )
        }
        composable("receive") {
            ReceiveScreen(
                onBack = { navigateBackOrHome() },
                onNavigateHistory = { navController.navigate("history") },
                autoAccept = autoAccept,
            )
        }
        composable("send") {
            SendScreen(
                onBack = {
                    // Clear injected URIs when leaving send screen
                    activeSharedUris = emptyList<Uri>()
                    navigateBackOrHome()
                },
                onNavigateHistory = { navController.navigate("history") },
                initialUris = activeSharedUris,
                initialQrUri = initialQrUri,
            )
        }
        composable("history") {
            HistoryScreen(
                onBack = { navigateBackOrHome() },
            )
        }
        composable("mirror/{deviceId}") { backStackEntry ->
            val deviceId = backStackEntry.arguments?.getString("deviceId") ?: ""
            MirrorScreen(
                deviceId = deviceId,
                onBack = { navigateBackOrHome() },
            )
        }
    }
}
