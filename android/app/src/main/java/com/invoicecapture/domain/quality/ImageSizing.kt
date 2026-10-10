package com.invoicecapture.domain.quality

import kotlin.math.roundToInt

data class PixelSize(val width: Int, val height: Int) {
    val longSide: Int get() = maxOf(width, height)
}

/** How to turn stored pixels upright for an EXIF orientation: rotate clockwise, then mirror. */
data class ExifTransform(val rotationDegrees: Int, val mirror: Boolean) {
    val swapsSides: Boolean get() = rotationDegrees == 90 || rotationDegrees == 270
}

/** Pure size arithmetic for the upload copy (3000 px) and the analysis copy (800 px). */
object ImageSizing {

    /** The size with the long side at most [maxLongSide], keeping the aspect ratio. Never upscales. */
    fun fitLongSide(width: Int, height: Int, maxLongSide: Int): PixelSize {
        require(width > 0 && height > 0 && maxLongSide > 0) { "sizes must be positive" }
        val long = maxOf(width, height)
        if (long <= maxLongSide) return PixelSize(width, height)
        val scale = maxLongSide.toDouble() / long
        return if (width >= height) {
            PixelSize(maxLongSide, maxOf(1, (height * scale).roundToInt()))
        } else {
            PixelSize(maxOf(1, (width * scale).roundToInt()), maxLongSide)
        }
    }

    /** The largest power-of-two decode sample size that still leaves the long side ≥ [targetLongSide]. */
    fun sampleSizeFor(width: Int, height: Int, targetLongSide: Int): Int {
        val long = maxOf(width, height)
        var sample = 1
        while (long / (sample * 2) >= targetLongSide) sample *= 2
        return sample
    }

    /** EXIF orientation tag (1–8) to a rotation and mirror; unknown values mean "as stored". */
    fun exifTransform(orientation: Int): ExifTransform = when (orientation) {
        2 -> ExifTransform(0, mirror = true)
        3 -> ExifTransform(180, mirror = false)
        4 -> ExifTransform(180, mirror = true)
        5 -> ExifTransform(90, mirror = true)
        6 -> ExifTransform(90, mirror = false)
        7 -> ExifTransform(270, mirror = true)
        8 -> ExifTransform(270, mirror = false)
        else -> ExifTransform(0, mirror = false)
    }

    /** The upright size once the EXIF orientation is applied. */
    fun orientedSize(width: Int, height: Int, orientation: Int): PixelSize =
        if (exifTransform(orientation).swapsSides) PixelSize(height, width) else PixelSize(width, height)
}
