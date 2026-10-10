package com.invoicecapture.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.invoicecapture.ui.theme.Industry

/** Industry's blueprint plate: a square hairline frame with registration marks on the corners. */
fun Modifier.plate(marks: Boolean = true, border: Color = Industry.Divider): Modifier = drawWithContent {
    drawContent()
    val stroke = 1.dp.toPx()
    val half = stroke / 2
    drawRect(
        color = border,
        topLeft = Offset(half, half),
        size = Size(size.width - stroke, size.height - stroke),
        style = Stroke(stroke),
    )
    if (marks) {
        val arm = 5.5.dp.toPx()
        for (x in listOf(0f, size.width)) {
            for (y in listOf(0f, size.height)) {
                drawLine(Industry.Mark, Offset(x - arm, y), Offset(x + arm, y), stroke)
                drawLine(Industry.Mark, Offset(x, y - arm), Offset(x, y + arm), stroke)
            }
        }
    }
}

@Composable
fun Plate(
    modifier: Modifier = Modifier,
    marks: Boolean = true,
    background: Color = Color.Transparent,
    contentPadding: Dp = 12.dp,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier.background(background).plate(marks).padding(contentPadding),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        content = content,
    )
}

/** Small upper-case heading (`.kicker`). */
@Composable
fun Kicker(text: String, modifier: Modifier = Modifier, color: Color = Industry.Muted) {
    Text(text.uppercase(), modifier = modifier, style = MaterialTheme.typography.labelMedium, color = color)
}

/** The Industry app bar: 48 dp back chevron (a text glyph), one-line title and sub-line, hairline below. */
@Composable
fun AppBar(
    title: String,
    subtitle: String? = null,
    onBack: (() -> Unit)? = null,
    actions: @Composable RowScope.() -> Unit = {},
) {
    Column(Modifier.fillMaxWidth().background(Industry.Ground).statusBarsPadding()) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (onBack != null) {
                Box(
                    Modifier.size(48.dp).clickable(onClickLabel = "Back", role = Role.Button, onClick = onBack),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("‹", fontSize = 32.sp, color = Industry.Ink)
                }
            } else {
                Spacer(Modifier.width(14.dp))
            }
            Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (!subtitle.isNullOrBlank()) {
                    Text(
                        subtitle,
                        style = MaterialTheme.typography.bodySmall.copy(fontSize = 12.sp),
                        color = Industry.Muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            actions()
        }
        HorizontalDivider(color = Industry.Divider)
    }
}

/** Full-width 56 dp primary action (`.btn-primary.btn-full`). */
@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Button(
        onClick = onClick,
        modifier = modifier.fillMaxWidth().heightIn(min = 56.dp),
        enabled = enabled,
        shape = RectangleShape,
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center)
    }
}

/** 48 dp hairline button (`.btn-secondary`). */
@Composable
fun SecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    color: Color = Industry.Ink,
) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier.heightIn(min = 48.dp),
        enabled = enabled,
        shape = RectangleShape,
        border = BorderStroke(1.dp, if (enabled) Industry.Neutral600 else Industry.Divider),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = color),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge.copy(fontSize = 15.sp), textAlign = TextAlign.Center)
    }
}

/** 48 dp text action for app bars and dialogs. */
@Composable
fun TextAction(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, color: Color? = null) {
    TextButton(onClick = onClick, modifier = modifier.heightIn(min = 48.dp), enabled = enabled, shape = RectangleShape) {
        Text(text, style = MaterialTheme.typography.labelLarge.copy(fontSize = 15.sp), color = color ?: Color.Unspecified)
    }
}

/** A plate with a 3 dp severity edge on the left (`.notice`). */
@Composable
fun Notice(
    title: String,
    body: String?,
    modifier: Modifier = Modifier,
    edge: Color = Industry.Accent900,
    background: Color = Color.Transparent,
) {
    Row(modifier.fillMaxWidth().height(IntrinsicSize.Min).background(background).plate(marks = false)) {
        Box(Modifier.width(3.dp).fillMaxHeight().background(edge))
        Column(Modifier.padding(horizontal = 10.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title.uppercase(), style = MaterialTheme.typography.labelMedium.copy(fontSize = 13.sp), color = Industry.Ink)
            if (!body.isNullOrBlank()) Text(body, style = MaterialTheme.typography.bodySmall, color = Industry.Ink)
        }
    }
}

@Composable
fun Tag(text: String, modifier: Modifier = Modifier, solid: Boolean = false, color: Color = Industry.Accent900) {
    Text(
        text,
        modifier = modifier
            .background(if (solid) color else Color.Transparent)
            .border(1.dp, color)
            .padding(horizontal = 8.dp, vertical = 2.dp),
        style = MaterialTheme.typography.labelSmall,
        color = if (solid) Industry.Ground else color,
        maxLines = 1,
    )
}

/** A labelled figure (`.readout`). */
@Composable
fun Figure(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = Industry.Ink) {
    Column(modifier) {
        Text(label.uppercase(), style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 1.sp), color = Industry.Muted)
        Text(
            value,
            style = MaterialTheme.typography.titleMedium.copy(fontSize = 19.sp, fontFeatureSettings = "tnum"),
            color = valueColor,
        )
    }
}

/** Determinate reading progress (`.bar`), no animation beyond the value changing. */
@Composable
fun ReadingBar(progress: Float, modifier: Modifier = Modifier) {
    LinearProgressIndicator(
        progress = { progress.coerceIn(0f, 1f) },
        modifier = modifier.fillMaxWidth().height(6.dp),
        color = Industry.Accent,
        trackColor = Industry.Rule,
        strokeCap = StrokeCap.Butt,
        gapSize = 0.dp,
        drawStopIndicator = {},
    )
}

@Composable
fun ConfirmDialog(
    title: String,
    text: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    /** null: a message with only the confirm button. */
    dismissLabel: String? = "Cancel",
    destructive: Boolean = false,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, style = MaterialTheme.typography.titleLarge) },
        text = { Text(text, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = {
            TextAction(confirmLabel, onConfirm, color = if (destructive) Industry.Red else MaterialTheme.colorScheme.primary)
        },
        dismissButton = dismissLabel?.let { label -> { TextAction(label, onDismiss) } },
        shape = RectangleShape,
        containerColor = Industry.Ground,
    )
}
