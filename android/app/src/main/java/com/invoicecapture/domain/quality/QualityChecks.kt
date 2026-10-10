package com.invoicecapture.domain.quality

import kotlin.math.ceil

/**
 * A grayscale picture as luminance values 0..255, row by row (index = y * width + x).
 * The quality maths works on this alone, so it runs in plain JVM unit tests.
 */
class LumaImage(val width: Int, val height: Int, val luma: IntArray) {
    init {
        require(width > 0 && height > 0) { "image must not be empty" }
        require(luma.size == width * height) { "expected ${width * height} values, got ${luma.size}" }
    }

    val pixelCount: Int get() = luma.size

    operator fun get(x: Int, y: Int): Int = luma[y * width + x]
}

/** A problem the phone can see in a photo (contract §6). [code] is what is stored in the queue. */
enum class QualityProblem(val code: String) {
    BLURRED("blur"),
    TOO_DARK("too_dark"),
    WASHED_OUT("washed_out"),
    FAINT("faint_print"),
    GLARE("glare");

    /** Plain words for the manager, e.g. "page 3 looks blurred". */
    fun describe(pageNo: Int): String = when (this) {
        BLURRED -> "page $pageNo looks blurred"
        TOO_DARK -> "page $pageNo is too dark"
        WASHED_OUT -> "page $pageNo looks washed out"
        FAINT -> "page $pageNo has faint print"
        GLARE -> "page $pageNo has glare"
    }

    companion object {
        fun fromCode(code: String): QualityProblem? = entries.firstOrNull { it.code == code }
    }
}

data class QualityMeasures(
    val sharpness: Double,
    val meanLuma: Double,
    val contrastSpread: Int,
    val glareFraction: Double,
)

data class QualityReport(val measures: QualityMeasures, val problems: List<QualityProblem>) {
    val ok: Boolean get() = problems.isEmpty()
}

/** Pure functions for the phone's photo checks (contract §6). */
object QualityChecks {

    fun assess(image: LumaImage, config: QualityConfig = QualityConfig.DEFAULT): QualityReport {
        val measures = measure(image, config)
        return QualityReport(measures, problems(measures, config))
    }

    fun measure(image: LumaImage, config: QualityConfig = QualityConfig.DEFAULT) = QualityMeasures(
        sharpness = laplacianVariance(image),
        meanLuma = meanLuma(image),
        contrastSpread = contrastSpread(image, config),
        glareFraction = glareFraction(image, config),
    )

    /** "Problem when" column of the contract table; every comparison is strict. */
    fun problems(m: QualityMeasures, config: QualityConfig = QualityConfig.DEFAULT): List<QualityProblem> =
        buildList {
            if (m.sharpness < config.minSharpness) add(QualityProblem.BLURRED)
            if (m.meanLuma < config.minMeanLuma) add(QualityProblem.TOO_DARK)
            if (m.meanLuma > config.maxMeanLuma) add(QualityProblem.WASHED_OUT)
            if (m.contrastSpread < config.minContrastSpread) add(QualityProblem.FAINT)
            if (m.glareFraction > config.maxGlareFraction) add(QualityProblem.GLARE)
        }

    /**
     * Variance of the 3×3 Laplacian [0 1 0; 1 −4 1; 0 1 0] over every pixel. Borders are
     * reflected like OpenCV's default (BORDER_REFLECT_101), so on the same pixels this equals
     * the laptop's `cv2.Laplacian(gray, cv2.CV_64F).var()`.
     */
    fun laplacianVariance(image: LumaImage): Double {
        val w = image.width
        val h = image.height
        val px = image.luma
        var sum = 0.0
        var sumSq = 0.0
        for (y in 0 until h) {
            val row = y * w
            val up = reflect101(y - 1, h) * w
            val down = reflect101(y + 1, h) * w
            for (x in 0 until w) {
                val left = reflect101(x - 1, w)
                val right = reflect101(x + 1, w)
                val lap = (px[up + x] + px[down + x] + px[row + left] + px[row + right] - 4 * px[row + x]).toDouble()
                sum += lap
                sumSq += lap * lap
            }
        }
        val n = image.pixelCount.toDouble()
        val mean = sum / n
        return (sumSq / n - mean * mean).coerceAtLeast(0.0)
    }

    fun meanLuma(image: LumaImage): Double {
        var sum = 0L
        for (v in image.luma) sum += v
        return sum.toDouble() / image.pixelCount
    }

    /** Nearest-rank percentile from a 256-bin histogram (values outside 0..255 are clamped). */
    fun percentile(image: LumaImage, percent: Double): Int =
        percentileOf(histogram(image), image.pixelCount, percent)

    /** Faint-print measure: 95th minus 5th luminance percentile. */
    fun contrastSpread(image: LumaImage, config: QualityConfig = QualityConfig.DEFAULT): Int {
        val hist = histogram(image)
        return percentileOf(hist, image.pixelCount, config.highPercentile) -
            percentileOf(hist, image.pixelCount, config.lowPercentile)
    }

    /**
     * Glare: split the picture into 16×16 px blocks (edge blocks are smaller). A block is
     * saturated when at least 25% of its pixels are ≥ 250. The result is the number of ≥ 250
     * pixels inside saturated blocks divided by all pixels, so a bright reflection counts while
     * scattered bright specks on white paper do not.
     */
    fun glareFraction(image: LumaImage, config: QualityConfig = QualityConfig.DEFAULT): Double {
        val w = image.width
        val h = image.height
        val px = image.luma
        val block = config.glareBlockSize
        var glarePixels = 0L
        var by = 0
        while (by < h) {
            val yEnd = minOf(by + block, h)
            var bx = 0
            while (bx < w) {
                val xEnd = minOf(bx + block, w)
                var bright = 0
                for (y in by until yEnd) {
                    val row = y * w
                    for (x in bx until xEnd) {
                        if (px[row + x] >= config.glareLuma) bright++
                    }
                }
                val blockPixels = (yEnd - by) * (xEnd - bx)
                if (bright > 0 && bright >= config.glareBlockMinSaturated * blockPixels) {
                    glarePixels += bright
                }
                bx += block
            }
            by += block
        }
        return glarePixels.toDouble() / image.pixelCount
    }

    private fun histogram(image: LumaImage): IntArray {
        val hist = IntArray(256)
        for (v in image.luma) hist[v.coerceIn(0, 255)]++
        return hist
    }

    private fun percentileOf(hist: IntArray, count: Int, percent: Double): Int {
        val rank = ceil(percent / 100.0 * count).toLong().coerceIn(1L, count.toLong())
        var cumulative = 0L
        for (value in 0..255) {
            cumulative += hist[value]
            if (cumulative >= rank) return value
        }
        return 255
    }

    internal fun reflect101(i: Int, n: Int): Int = when {
        n == 1 -> 0
        i < 0 -> -i
        i >= n -> 2 * n - 2 - i
        else -> i
    }
}

/** Colour to luminance. */
object Luma {
    /**
     * ARGB pixels (as from `Bitmap.getPixels`) to luminance 0..255 with the ITU-R BT.601
     * weights OpenCV uses for RGB→GRAY: 0.299 R + 0.587 G + 0.114 B, rounded.
     */
    fun fromArgb(argb: IntArray): IntArray = IntArray(argb.size) { i ->
        val p = argb[i]
        val r = (p shr 16) and 0xFF
        val g = (p shr 8) and 0xFF
        val b = p and 0xFF
        (299 * r + 587 * g + 114 * b + 500) / 1000
    }
}
