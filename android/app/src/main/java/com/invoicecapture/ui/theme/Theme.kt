package com.invoicecapture.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.DeviceFontFamilyName
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Industry design-system tokens (invoice-pipeline/app/static/industry.css) as Compose colours. */
object Industry {
    val Ground = Color(0xFFF2F2F3)
    val Surface = Color(0xFFE9E9EA)
    val Ink = Color(0xFF1D1F20)
    val Accent = Color(0xFF5980A6)
    val Accent100 = Color(0xFFEEF6FF)
    val Accent200 = Color(0xFFD6EBFF)
    val Accent700 = Color(0xFF416180)
    val Accent800 = Color(0xFF2C455D)
    val Accent900 = Color(0xFF1D2D3D)
    val Neutral600 = Color(0xFF7A7A7D)

    /** Hairlines: ink at 16%; softer internal rules at 12%. */
    val Divider = Color(0x291D1F20)
    val Rule = Color(0x1F1D1F20)

    /** Secondary text: ink at about 72% on the ground (6.5:1). */
    val Muted = Color(0xFF595A5B)

    /** Registration marks on plates: ink at 55%. */
    val Mark = Color(0xFF7D7E7F)

    /** Corrections tab and its content: the red background the user asked for. */
    val Red = Color(0xFFB3261E)
    val RedWash = Color(0xFFF9DEDC)
    val OnRedWash = Color(0xFF410E0B)

    /** Starred lines (★): a 12% accent wash. */
    val StarWash = Color(0x1F5980A6)
}

/** Industry has no rounded corners. */
private val SquareShapes = Shapes(
    extraSmall = RoundedCornerShape(0.dp),
    small = RoundedCornerShape(0.dp),
    medium = RoundedCornerShape(0.dp),
    large = RoundedCornerShape(0.dp),
    extraLarge = RoundedCornerShape(0.dp),
)

/**
 * Condensed headings. Barlow Condensed is not bundled (no web fonts, as in the web review app),
 * so headings use the phone's own condensed face and fall back to the system sans-serif.
 */
val Condensed = FontFamily(
    Font(DeviceFontFamilyName("sans-serif-condensed"), weight = FontWeight.Normal),
    Font(DeviceFontFamilyName("sans-serif-condensed"), weight = FontWeight.Medium),
    Font(DeviceFontFamilyName("sans-serif-condensed"), weight = FontWeight.SemiBold),
    Font(DeviceFontFamilyName("sans-serif-condensed"), weight = FontWeight.Bold),
)

private fun heading(size: Int, line: Int, spacing: Double = 0.0) = TextStyle(
    fontFamily = Condensed,
    fontWeight = FontWeight.SemiBold,
    fontSize = size.sp,
    lineHeight = line.sp,
    letterSpacing = spacing.sp,
)

private val IndustryTypography = Typography(
    headlineSmall = heading(26, 30),
    titleLarge = heading(21, 25),
    titleMedium = heading(18, 22),
    titleSmall = heading(15, 19),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 13.sp, lineHeight = 18.sp),
    labelLarge = heading(17, 20, 0.2),
    labelMedium = heading(12, 16, 1.2),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 14.sp, letterSpacing = 0.4.sp),
)

/**
 * Filled buttons use accent-700 rather than the base accent so their labels reach 4.5:1
 * contrast on budget screens in a bright store; the base accent marks selection and progress.
 */
private val IndustryColors = lightColorScheme(
    primary = Industry.Accent700,
    onPrimary = Industry.Ground,
    primaryContainer = Industry.Accent100,
    onPrimaryContainer = Industry.Accent800,
    secondary = Industry.Accent,
    onSecondary = Industry.Ground,
    secondaryContainer = Industry.Accent200,
    onSecondaryContainer = Industry.Accent900,
    tertiary = Industry.Accent900,
    onTertiary = Industry.Ground,
    background = Industry.Ground,
    onBackground = Industry.Ink,
    surface = Industry.Ground,
    onSurface = Industry.Ink,
    surfaceVariant = Industry.Surface,
    onSurfaceVariant = Industry.Muted,
    surfaceContainerLowest = Color(0xFFF7F7F8),
    surfaceContainerLow = Color(0xFFEFEFF0),
    surfaceContainer = Industry.Surface,
    surfaceContainerHigh = Color(0xFFE4E4E5),
    surfaceContainerHighest = Color(0xFFDEDEDF),
    outline = Industry.Neutral600,
    outlineVariant = Color(0xFFD0D0D1),
    error = Industry.Red,
    onError = Color.White,
    errorContainer = Industry.RedWash,
    onErrorContainer = Industry.OnRedWash,
    inverseSurface = Industry.Ink,
    inverseOnSurface = Industry.Ground,
    inversePrimary = Industry.Accent200,
    scrim = Color.Black,
)

@Composable
fun InvoiceCaptureTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = IndustryColors,
        typography = IndustryTypography,
        shapes = SquareShapes,
        content = content,
    )
}
