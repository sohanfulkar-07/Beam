package com.photobeam.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.photobeam.app.MainActivity
import com.photobeam.app.R
import com.photobeam.app.data.ConnectionManager

/**
 * Foreground service that keeps the ConnectionManager alive in the background.
 *
 * On aggressive Android OEM builds (OnePlus/Oppo Hans, Xiaomi MIUI, etc.),
 * background processes are frozen even with wake locks held. A foreground service
 * with a visible notification is the only reliable way to prevent this.
 *
 * Start this service when:
 *   - The app goes to the background while a session is active (in HomeScreen)
 *   - The ReceiveScreen is open (waiting for a connection)
 *
 * Stop this service when:
 *   - No active sessions and not in receive/send mode
 *   - The app is fully destroyed
 */
class ConnectionForegroundService : Service() {

    companion object {
        private const val TAG = "PhotoBeam"
        private const val CHANNEL_ID = "photobeam_connection_channel"
        private const val NOTIFICATION_ID = 1001

        const val ACTION_START = "com.photobeam.app.service.START_CONNECTION"
        const val ACTION_STOP = "com.photobeam.app.service.STOP_CONNECTION"
        const val EXTRA_STATUS = "status_text"

        private var isRunning = false

        fun start(context: Context, statusText: String = "Keeping connection alive…") {
            if (isRunning) {
                // Just update the notification text
                update(context, statusText)
                return
            }
            val intent = Intent(context, ConnectionForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_STATUS, statusText)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun update(context: Context, statusText: String) {
            val intent = Intent(context, ConnectionForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_STATUS, statusText)
            }
            context.startService(intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, ConnectionForegroundService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }

        fun isServiceRunning() = isRunning
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        Log.i(TAG, "[DIAG] ConnectionForegroundService created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                Log.i(TAG, "[DIAG] ConnectionForegroundService stopping")
                isRunning = false
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                val statusText = intent?.getStringExtra(EXTRA_STATUS) ?: "Keeping connection alive…"
                val notification = buildNotification(statusText)
                startForeground(NOTIFICATION_ID, notification)
                isRunning = true
                Log.i(TAG, "[DIAG] ConnectionForegroundService started: $statusText")

                // Ensure ConnectionManager is started
                try {
                    ConnectionManager.getInstance(applicationContext).let { cm ->
                        if (!cm.isRunning) cm.start()
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Could not verify ConnectionManager state: ${e.message}")
                }
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        isRunning = false
        Log.i(TAG, "[DIAG] ConnectionForegroundService destroyed")
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "PhotoBeam Connection",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Keeps PhotoBeam connected in the background"
                setShowBadge(false)
            }
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(statusText: String): Notification {
        val tapIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
        }
        val pi = PendingIntent.getActivity(
            this, 0, tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("PhotoBeam")
            .setContentText(statusText)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentIntent(pi)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }
}
