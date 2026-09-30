package com.photobeam.app.protocol

import com.photobeam.app.transport.Transport
import java.util.concurrent.ConcurrentHashMap

data class TransportSlot(
    val transport: Transport,
    var weight: Double = 1.0,
    var lastMeasured: Long = 0L,
    var consecutiveFailures: Int = 0,
    var enabled: Boolean = true,
)

/**
 * Multi-Path Scheduler for Android.
 * Mirrors protocol/src/scheduler.py.
 * Dynamically distributes chunks across available transports based on throughput.
 * Handles transport failure and fallback.
 */
class Scheduler {
    private val slots = ConcurrentHashMap<String, TransportSlot>()
    private val counters = ConcurrentHashMap<String, Double>()
    private val lock = Any()
    private var lastMeasure = 0L

    companion object {
        const val MEASURE_INTERVAL_MS = 500L
        const val MIN_WEIGHT = 0.05
        const val MAX_FAILURES = 3
    }

    fun addTransport(transport: Transport) {
        synchronized(lock) {
            slots[transport.transportId] = TransportSlot(transport)
            counters[transport.transportId] = 0.0
        }
    }

    fun removeTransport(transportId: String) {
        synchronized(lock) {
            slots.remove(transportId)
            counters.remove(transportId)
        }
    }

    fun reportFailure(transportId: String) {
        synchronized(lock) {
            val slot = slots[transportId] ?: return
            slot.consecutiveFailures++
            if (slot.consecutiveFailures >= MAX_FAILURES) {
                slot.enabled = false
            }
        }
    }

    fun reportSuccess(transportId: String) {
        synchronized(lock) {
            slots[transportId]?.consecutiveFailures = 0
        }
    }

    fun markReconnected(transportId: String) {
        synchronized(lock) {
            val slot = slots[transportId] ?: return
            slot.enabled = true
            slot.consecutiveFailures = 0
        }
    }

    fun nextTransport(): Transport? {
        synchronized(lock) {
            maybeRemeasure()
            val active = slots.filter { (_, slot) -> slot.enabled && slot.transport.isConnected() }
            if (active.isEmpty()) return null
            if (active.size == 1) return active.values.first().transport

            // Weighted round-robin deficit counter
            for ((tid, slot) in active) {
                counters[tid] = (counters[tid] ?: 0.0) + slot.weight
            }

            val bestEntry = active.maxByOrNull { (tid, _) -> counters[tid] ?: 0.0 } ?: return null
            counters[bestEntry.key] = (counters[bestEntry.key] ?: 0.0) - 1.0
            return bestEntry.value.transport
        }
    }

    fun availableTransports(): List<Transport> = synchronized(lock) {
        slots.values.filter { it.enabled && it.transport.isConnected() }.map { it.transport }
    }

    fun hasAnyTransport(): Boolean = synchronized(lock) {
        slots.values.any { it.enabled && it.transport.isConnected() }
    }

    fun throughputSummary(): Map<String, Double> = synchronized(lock) {
        slots.filter { it.value.transport.isConnected() }
            .mapValues { it.value.transport.throughputBps() }
    }

    private fun maybeRemeasure() {
        val now = System.currentTimeMillis()
        if (now - lastMeasure < MEASURE_INTERVAL_MS) return
        lastMeasure = now

        val speeds = mutableMapOf<String, Double>()
        val unmeasured = mutableListOf<String>()

        for ((tid, slot) in slots) {
            if (slot.enabled && slot.transport.isConnected()) {
                val bps = slot.transport.throughputBps()
                if (bps > 0.0) {
                    speeds[tid] = bps
                } else {
                    unmeasured.add(tid)
                }
            }
        }

        if (speeds.isEmpty()) {
            val eq = 1.0 / slots.size.coerceAtLeast(1)
            slots.values.forEach { it.weight = eq }
            return
        }

        val avgSpeed = speeds.values.average()
        for (tid in unmeasured) {
            speeds[tid] = avgSpeed
        }

        val total = speeds.values.sum()
        for ((tid, bps) in speeds) {
            val rawWeight = bps / total
            slots[tid]?.let {
                it.weight = maxOf(rawWeight, MIN_WEIGHT)
                it.lastMeasured = now
            }
        }
    }
}
