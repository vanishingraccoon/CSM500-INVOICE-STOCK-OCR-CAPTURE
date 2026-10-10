package com.invoicecapture.domain.quality

/**
 * Every photo-quality threshold in one place (contract §6). These are starting values, to be
 * calibrated on real photos; the laptop keeps its own copy and repeats the checks.
 */
data class QualityConfig(
    /** The checks run on a grayscale copy downscaled to this long side, in pixels. */
    val analysisLongSide: Int = 800,

    /** Blur: variance of a 3×3 Laplacian below this. */
    val minSharpness: Double = 60.0,

    /** Too dark: mean luminance below this. */
    val minMeanLuma: Double = 70.0,

    /** Washed out: mean luminance above this. */
    val maxMeanLuma: Double = 215.0,

    /** Faint print: 95th − 5th luminance percentile below this. */
    val minContrastSpread: Int = 80,
    val lowPercentile: Double = 5.0,
    val highPercentile: Double = 95.0,

    /** Glare: pixels at or above [glareLuma], counted inside blocks of [glareBlockSize] px
     *  whose share of such pixels is at least [glareBlockMinSaturated]; problem above [maxGlareFraction]. */
    val glareLuma: Int = 250,
    val glareBlockSize: Int = 16,
    val glareBlockMinSaturated: Double = 0.25,
    val maxGlareFraction: Double = 0.02,

    /** The upload copy: long side in pixels and JPEG quality. */
    val uploadLongSide: Int = 3000,
    val uploadJpegQuality: Int = 90,
) {
    companion object {
        val DEFAULT = QualityConfig()
    }
}
