package com.photobeam.app.ui.components

import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.ripple.rememberRipple
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.photobeam.app.ui.theme.*

/**
 * TactileCard — Frosted glass surface with soft tactile depth and hairline cyan/white border.
 */
@Composable
fun TactileCard(
    modifier: Modifier = Modifier,
    cornerRadius: Dp = 20.dp,
    backgroundColor: Color = Surface,
    borderColor: Color = CardBorder,
    elevation: Dp = 6.dp,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit
) {
    val shape = RoundedCornerShape(cornerRadius)
    val clickModifier = if (onClick != null) {
        Modifier.clickable(onClick = onClick)
    } else Modifier

    Card(
        modifier = modifier
            .shadow(elevation, shape, ambientColor = Color(0x66000000), spotColor = Color(0x3300E5FF))
            .border(1.dp, borderColor, shape)
            .then(clickModifier),
        shape = shape,
        colors = CardDefaults.cardColors(containerColor = backgroundColor),
    ) {
        Column(
            modifier = Modifier.padding(18.dp),
            content = content
        )
    }
}

/**
 * TactilePillButton — Neo-tactile pill button inspired by Reference 1 ("Defart" / "active").
 */
@Composable
fun TactilePillButton(
    text: String,
    active: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: (@Composable () -> Unit)? = null,
    minHeight: Dp = 44.dp,
) {
    val interactionSource = remember { MutableInteractionSource() }
    val isPressed by interactionSource.collectIsPressedAsState()
    val scale = if (isPressed) 0.96f else 1.0f

    val shape = RoundedCornerShape(22.dp)
    val backgroundBrush = if (active) ActivePillBrush else InactivePillBrush
    val borderColor = if (active) PrimaryGlow.copy(alpha = 0.6f) else TactileDefaultBorder
    val textColor = if (active) Color.White else OnSurface

    Box(
        modifier = modifier
            .scale(scale)
            .heightIn(min = minHeight)
            .clip(shape)
            .background(backgroundBrush)
            .border(1.dp, borderColor, shape)
            .clickable(
                interactionSource = interactionSource,
                indication = null,
                onClick = onClick
            )
            .padding(horizontal = 18.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center,
        ) {
            if (icon != null) {
                icon()
                Spacer(Modifier.width(8.dp))
            }
            Text(
                text = text,
                color = textColor,
                fontSize = 14.sp,
                fontWeight = if (active) FontWeight.Bold else FontWeight.SemiBold
            )
        }
    }
}

/**
 * TactileGlowRing — Circular action / status indicator with cyan glowing rim.
 */
@Composable
fun TactileGlowRing(
    modifier: Modifier = Modifier,
    size: Dp = 56.dp,
    ringColor: Color = PrimaryGlow,
    pulse: Boolean = false,
    content: @Composable BoxScope.() -> Unit
) {
    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val alphaAnim by if (pulse) {
        infiniteTransition.animateFloat(
            initialValue = 0.5f,
            targetValue = 1.0f,
            animationSpec = infiniteRepeatable(
                animation = tween(1200, easing = FastOutSlowInEasing),
                repeatMode = RepeatMode.Reverse
            ),
            label = "pulseAlpha"
        )
    } else {
        remember { mutableStateOf(1.0f) }
    }

    Box(
        modifier = modifier
            .size(size)
            .clip(CircleShape)
            .background(SurfaceElevated)
            .border(2.5.dp, ringColor.copy(alpha = alphaAnim), CircleShape)
            .shadow(8.dp, CircleShape, ambientColor = ringColor.copy(alpha = 0.4f), spotColor = ringColor),
        contentAlignment = Alignment.Center,
        content = content
    )
}

/**
 * TactileBadge — Status pill tag with soft translucent colored fill.
 */
@Composable
fun TactileBadge(
    text: String,
    badgeColor: Color,
    modifier: Modifier = Modifier,
    textColor: Color = badgeColor
) {
    val shape = RoundedCornerShape(12.dp)
    Box(
        modifier = modifier
            .clip(shape)
            .background(badgeColor.copy(alpha = 0.16f))
            .border(1.dp, badgeColor.copy(alpha = 0.35f), shape)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = text,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            color = textColor
        )
    }
}

/**
 * TactileProgressBar — Sleek progress bar with cyan-to-electric-blue gradient fill.
 */
@Composable
fun TactileProgressBar(
    progress: Float,
    modifier: Modifier = Modifier,
    height: Dp = 10.dp
) {
    val coercedProgress = progress.coerceIn(0f, 1f)
    val shape = RoundedCornerShape(height / 2)

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(height)
            .clip(shape)
            .background(SurfaceInset)
            .border(1.dp, CardBorderSubtle, shape)
    ) {
        if (coercedProgress > 0f) {
            Box(
                modifier = Modifier
                    .fillMaxHeight()
                    .fillMaxWidth(coercedProgress)
                    .clip(shape)
                    .background(
                        Brush.horizontalGradient(
                            listOf(PrimaryGlow, PrimaryLight, Primary)
                        )
                    )
            )
        }
    }
}
