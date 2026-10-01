package com.photobeam.app.ui.theme

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

// ── Neo-Tactile Dark Navy Design Tokens ──────────────────────────────────────
val Background = Color(0xFF090D16)
val BackgroundGradientTop = Color(0xFF0F182B)
val BackgroundGradientBottom = Color(0xFF060911)

val Surface = Color(0xFF121A2D)
val SurfaceElevated = Color(0xFF17223A)
val SurfaceVariant = Color(0xFF1C2844)
val SurfaceInset = Color(0xFF0B101E)

val CardBorder = Color(0x3338BDF8)           // 20% cyan hairline border
val CardBorderSubtle = Color(0x1FFFFFFF)     // 12% white highlight border
val CardGlass = Color(0xCC121A2D)            // 80% opacity dark navy glass

val Primary = Color(0xFF2563EB)              // Vibrant royal blue
val PrimaryLight = Color(0xFF3B82F6)         // Electric blue
val PrimaryGlow = Color(0xFF00E5FF)          // Soft neon cyan
val CyanAccent = Color(0xFF38BDF8)           // Sky cyan

val Secondary = Color(0xFF10B981)            // Mint / Success
val Warning = Color(0xFFF59E0B)              // Amber
val Error = Color(0xFFEF4444)                // Crimson
val Outline = Color(0xFF22304C)

val TactileDefault = Color(0xFF182236)
val TactileDefaultBorder = Color(0x28FFFFFF)

val OnBackground = Color(0xFFF8FAFC)
val OnSurface = Color(0xFFE2E8F0)
val OnSurfaceVariant = Color(0xFF94A3B8)

// ── Gradients ────────────────────────────────────────────────────────────────
val AppBackgroundBrush = Brush.verticalGradient(
    colors = listOf(BackgroundGradientTop, Background, BackgroundGradientBottom)
)

val ActivePillBrush = Brush.horizontalGradient(
    colors = listOf(Color(0xFF2563EB), Color(0xFF3B82F6))
)

val InactivePillBrush = Brush.verticalGradient(
    colors = listOf(Color(0xFF1E2A42), Color(0xFF141C2E))
)

val CyanGlowBrush = Brush.radialGradient(
    colors = listOf(Color(0x3300E5FF), Color(0x0000E5FF))
)

val CardHighlightBrush = Brush.verticalGradient(
    colors = listOf(Color(0x18FFFFFF), Color(0x00FFFFFF))
)

private val DarkColorScheme = darkColorScheme(
    primary = PrimaryLight,
    onPrimary = Color.White,
    secondary = Secondary,
    background = Background,
    surface = Surface,
    surfaceVariant = SurfaceVariant,
    onBackground = OnBackground,
    onSurface = OnSurface,
    onSurfaceVariant = OnSurfaceVariant,
    error = Error,
    outline = Outline,
)

@Composable
fun PhotoBeamTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColorScheme,
        content = content,
    )
}
