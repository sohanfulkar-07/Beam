package com.photobeam.app.mirror

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import com.photobeam.app.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * ScreenCaptureService — Android Foreground Service for live screen streaming.
 * Uses MediaProjection + VirtualDisplay + ImageReader to capture screen frames,
 * encodes them, and streams them over a local TCP socket to the paired Windows receiver.
 */
class ScreenCaptureService : Service() {

    private val tag = "ScreenCaptureService"
    private val scope = CoroutineScope(Dispatchers.IO + Job())

    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var streamSocket: Socket? = null

    private var isStreaming = false

    companion object {
        const val ACTION_START = "com.photobeam.app.mirror.START"
        const val ACTION_STOP = "com.photobeam.app.mirror.STOP"
        const val EXTRA_RESULT_CODE = "extra_result_code"
        const val EXTRA_RESULT_DATA = "extra_result_data"
        const val EXTRA_PEER_HOST = "extra_peer_host"
        const val EXTRA_PEER_PORT = "extra_peer_port"

        private const val CHANNEL_ID = "photobeam_mirror_stream"
        private const val NOTIFICATION_ID = 47478
        private val MAGIC_HEADER = byteArrayOf(0x50, 0x42, 0x4D, 0x53) // PBMS
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent == null) return START_NOT_STICKY

        when (intent.action) {
            ACTION_START -> {
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
                val resultData = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(EXTRA_RESULT_DATA)
                }
                val host = intent.getStringExtra(EXTRA_PEER_HOST) ?: "127.0.0.1"
                val port = intent.getIntExtra(EXTRA_PEER_PORT, 47478)

                if (resultCode != 0 && resultData != null) {
                    startForegroundNotification()
                    startStreaming(resultCode, resultData, host, port)
                }
            }
            ACTION_STOP -> {
                stopStreaming()
                stopSelf()
            }
        }

        return START_NOT_STICKY
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "PhotoBeam Screen Mirroring",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Active screen mirroring session"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun startForegroundNotification() {
        val stopIntent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPending = PendingIntent.getService(
            this, 0, stopIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val openIntent = Intent(this, MainActivity::class.java)
        val openPending = PendingIntent.getActivity(
            this, 0, openIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification: Notification = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("PhotoBeam Screen Mirroring")
                .setContentText("Live streaming screen to paired PC")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .setContentIntent(openPending)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop Sharing", stopPending)
                .setOngoing(true)
                .build()
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
                .setContentTitle("PhotoBeam Screen Mirroring")
                .setContentText("Live streaming screen to paired PC")
                .setSmallIcon(android.R.drawable.ic_menu_camera)
                .setContentIntent(openPending)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop Sharing", stopPending)
                .setOngoing(true)
                .build()
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startStreaming(resultCode: Int, resultData: Intent, host: String, port: Int) {
        if (isStreaming) return
        isStreaming = true

        scope.launch {
            try {
                val mpManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                mediaProjection = mpManager.getMediaProjection(resultCode, resultData)

                val wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
                val metrics = DisplayMetrics()
                @Suppress("DEPRECATION")
                wm.defaultDisplay.getRealMetrics(metrics)

                // Scale down slightly for responsive 30+ FPS low-latency streaming
                val targetWidth = 720
                val targetHeight = (((targetWidth.toFloat() / metrics.widthPixels) * metrics.heightPixels).toInt() / 2) * 2

                imageReader = ImageReader.newInstance(targetWidth, targetHeight, PixelFormat.RGBA_8888, 2)

                virtualDisplay = mediaProjection?.createVirtualDisplay(
                    "PhotoBeamMirrorDisplay",
                    targetWidth,
                    targetHeight,
                    metrics.densityDpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    imageReader?.surface,
                    null,
                    null
                )

                // Connect to Windows mirror receiver
                streamSocket = Socket()
                streamSocket?.connect(InetSocketAddress(host, port), 5000)
                val out = streamSocket?.getOutputStream() ?: return@launch

                var inFlight = false

                imageReader?.setOnImageAvailableListener({ reader ->
                    if (!isStreaming || inFlight) {
                        // Drop frame if previous is still writing (backpressure prevention)
                        reader.acquireLatestImage()?.close()
                        return@setOnImageAvailableListener
                    }

                    val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
                    inFlight = true

                    scope.launch {
                        try {
                            val planes = image.planes
                            val buffer = planes[0].buffer
                            val pixelStride = planes[0].pixelStride
                            val rowStride = planes[0].rowStride
                            val rowPadding = rowStride - pixelStride * targetWidth

                            val bmp = Bitmap.createBitmap(
                                targetWidth + rowPadding / pixelStride,
                                targetHeight,
                                Bitmap.Config.ARGB_8888
                            )
                            bmp.copyPixelsFromBuffer(buffer)
                            image.close()

                            val croppedBmp = if (rowPadding > 0) {
                                Bitmap.createBitmap(bmp, 0, 0, targetWidth, targetHeight).also { bmp.recycle() }
                            } else {
                                bmp
                            }

                            val stream = ByteArrayOutputStream()
                            croppedBmp.compress(Bitmap.CompressFormat.JPEG, 75, stream)
                            croppedBmp.recycle()
                            val frameBytes = stream.toByteArray()

                            // Packet header: 4 bytes MAGIC + 4 bytes length
                            val headerBuf = ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN)
                            headerBuf.put(MAGIC_HEADER)
                            headerBuf.putInt(frameBytes.size)

                            synchronized(out) {
                                out.write(headerBuf.array())
                                out.write(frameBytes)
                                out.flush()
                            }
                        } catch (e: Exception) {
                            Log.d(tag, "Frame encode/send failed: ${e.message}")
                        } finally {
                            inFlight = false
                        }
                    }
                }, null)

            } catch (e: Exception) {
                Log.e(tag, "Screen streaming error: ${e.message}", e)
                stopStreaming()
            }
        }
    }

    private fun stopStreaming() {
        isStreaming = false
        try {
            virtualDisplay?.release()
            virtualDisplay = null
            imageReader?.close()
            imageReader = null
            mediaProjection?.stop()
            mediaProjection = null
            streamSocket?.close()
            streamSocket = null
        } catch (e: Exception) {
            Log.w(tag, "Error closing stream resources: ${e.message}")
        }
        stopForeground(true)
    }

    override fun onDestroy() {
        stopStreaming()
        super.onDestroy()
    }
}
