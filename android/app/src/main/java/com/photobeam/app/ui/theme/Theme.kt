package com.photobeam.app.ui.theme

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val Background = Color(0xFF0F1117)
val Surface = Color(0xFF1A1F2E)
val SurfaceVariant = Color(0xFF1E293B)
val OnBackground = Color(0xFFE2E8F0)
val OnSurface = Color(0xFFCBD5E1)
val OnSurfaceVariant = Color(0xFF94A3B8)
val Primary = Color(0xFF3B82F6)
val PrimaryVariant = Color(0xFF6366F1)
val Secondary = Color(0xFF4ADE80)
val Error = Color(0xFFF87171)
val Outline = Color(0xFF2D3748)

private val DarkColorScheme = darkColorScheme(
    primary = Primary,
    secondary = Secondary,
    background = Background,
    surface = Surface,
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
