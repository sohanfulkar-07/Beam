package com.photobeam.app

import android.app.Application
import android.content.SharedPreferences
import java.util.UUID

class PhotoBeamApp : Application() {

    companion object {
        lateinit var instance: PhotoBeamApp
            private set
    }

    /** Persistent device ID, generated once on install. */
    val deviceId: String by lazy {
        val prefs: SharedPreferences = getSharedPreferences("photobeam_prefs", MODE_PRIVATE)
        prefs.getString("device_id", null) ?: run {
            val id = UUID.randomUUID().toString()
            prefs.edit().putString("device_id", id).apply()
            id
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }
}
