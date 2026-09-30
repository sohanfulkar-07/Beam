package com.photobeam.app.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

data class TransferRecord(
    val id: String = UUID.randomUUID().toString(),
    val direction: String,        // "sent" or "received"
    val files: List<String>,
    val totalBytes: Long,
    val timestamp: Long = System.currentTimeMillis(),
    val durationSec: Double = 0.0,
    val status: String,           // "completed", "interrupted", "failed"
    val transportType: String,    // "Wi-Fi", "USB", "Wi-Fi + USB"
    val errorReason: String = "",
) {
    fun toJson(): JSONObject {
        val obj = JSONObject()
        obj.put("id", id)
        obj.put("direction", direction)
        val arr = JSONArray()
        files.forEach { arr.put(it) }
        obj.put("files", arr)
        obj.put("totalBytes", totalBytes)
        obj.put("timestamp", timestamp)
        obj.put("durationSec", durationSec)
        obj.put("status", status)
        obj.put("transportType", transportType)
        obj.put("errorReason", errorReason)
        return obj
    }

    companion object {
        fun fromJson(obj: JSONObject): TransferRecord {
            val filesList = mutableListOf<String>()
            val arr = obj.optJSONArray("files")
            if (arr != null) {
                for (i in 0 until arr.length()) {
                    filesList.add(arr.getString(i))
                }
            }
            return TransferRecord(
                id = obj.optString("id", UUID.randomUUID().toString()),
                direction = obj.optString("direction", "sent"),
                files = filesList,
                totalBytes = obj.optLong("totalBytes", 0L),
                timestamp = obj.optLong("timestamp", System.currentTimeMillis()),
                durationSec = obj.optDouble("durationSec", 0.0),
                status = obj.optString("status", "completed"),
                transportType = obj.optString("transportType", "Wi-Fi"),
                errorReason = obj.optString("errorReason", ""),
            )
        }
    }
}

class TransferHistoryManager private constructor(context: Context) {
    private val historyFile = File(context.applicationContext.filesDir, "transfer_history.json")
    private val lock = Any()

    companion object {
        @Volatile
        private var instance: TransferHistoryManager? = null

        fun getInstance(context: Context): TransferHistoryManager {
            return instance ?: synchronized(this) {
                instance ?: TransferHistoryManager(context).also { instance = it }
            }
        }
    }

    fun getRecords(): List<TransferRecord> = synchronized(lock) {
        if (!historyFile.exists()) return emptyList()
        return try {
            val content = historyFile.readText(Charsets.UTF_8)
            val arr = JSONArray(content)
            val list = mutableListOf<TransferRecord>()
            for (i in 0 until arr.length()) {
                list.add(TransferRecord.fromJson(arr.getJSONObject(i)))
            }
            list
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun addRecord(record: TransferRecord) = synchronized(lock) {
        val records = getRecords().toMutableList()
        records.add(0, record)
        val capped = records.take(200)
        try {
            val arr = JSONArray()
            capped.forEach { arr.put(it.toJson()) }
            val tmp = File(historyFile.parentFile, "${historyFile.name}.tmp")
            tmp.writeText(arr.toString(2), Charsets.UTF_8)
            tmp.renameTo(historyFile)
        } catch (_: Exception) {}
    }

    fun clear() = synchronized(lock) {
        try {
            if (historyFile.exists()) {
                historyFile.delete()
            }
        } catch (_: Exception) {}
    }
}
