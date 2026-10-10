package com.invoicecapture.domain.quality

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class QualityChecksTest {

    private val config = QualityConfig.DEFAULT

    private fun image(w: Int, h: Int, f: (x: Int, y: Int) -> Int) =
        LumaImage(w, h, IntArray(w * h) { i -> f(i % w, i / w) })

    /** A text-like page: white paper crossed by dark 2-px strokes every 8 px. */
    private fun sharpPage(w: Int = 400, h: Int = 300) =
        image(w, h) { x, y -> if (x % 8 < 2 || y % 8 < 2) 30 else 235 }

    /** Separable box blur with clamped edges. */
    private fun boxBlur(src: LumaImage, radius: Int): LumaImage {
        val w = src.width
        val h = src.height
        val tmp = IntArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            var sum = 0
            for (dx in -radius..radius) sum += src[(x + dx).coerceIn(0, w - 1), y]
            tmp[y * w + x] = sum / (2 * radius + 1)
        }
        val out = IntArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            var sum = 0
            for (dy in -radius..radius) sum += tmp[(y + dy).coerceIn(0, h - 1) * w + x]
            out[y * w + x] = sum / (2 * radius + 1)
        }
        return LumaImage(w, h, out)
    }

    @Test
    fun laplacianVarianceOfOneBrightPixelIsExact() {
        // Centre −400, four neighbours +100, twenty zeros: mean 0, variance (160000 + 40000) / 25.
        val img = image(5, 5) { x, y -> if (x == 2 && y == 2) 100 else 0 }
        assertEquals(8000.0, QualityChecks.laplacianVariance(img), 1e-9)
    }

    @Test
    fun uniformImageHasNoSharpness() {
        assertEquals(0.0, QualityChecks.laplacianVariance(image(50, 40) { _, _ -> 128 }), 1e-9)
    }

    @Test
    fun sharpPageIsNotBlurred() {
        val report = QualityChecks.assess(sharpPage(), config)
        assertTrue("sharpness ${report.measures.sharpness}", report.measures.sharpness > 1000.0)
        assertTrue(report.ok)
    }

    @Test
    fun blurredPageIsBlurred() {
        val blurred = boxBlur(boxBlur(sharpPage(), 4), 4)
        val report = QualityChecks.assess(blurred, config)
        assertTrue("sharpness ${report.measures.sharpness}", report.measures.sharpness < config.minSharpness)
        assertTrue(QualityProblem.BLURRED in report.problems)
    }

    @Test
    fun darkPageIsTooDark() {
        val dark = image(200, 200) { x, y -> if ((x + y) % 10 < 5) 20 else 60 }
        val report = QualityChecks.assess(dark, config)
        assertEquals(40.0, report.measures.meanLuma, 1e-9)
        assertTrue(QualityProblem.TOO_DARK in report.problems)
        assertFalse(QualityProblem.WASHED_OUT in report.problems)
    }

    @Test
    fun overexposedPageIsWashedOut() {
        val bright = image(200, 200) { x, y -> 225 + (x + y) % 31 }
        val report = QualityChecks.assess(bright, config)
        assertTrue("mean ${report.measures.meanLuma}", report.measures.meanLuma > config.maxMeanLuma)
        assertTrue(QualityProblem.WASHED_OUT in report.problems)
    }

    @Test
    fun ordinaryPageHasGoodBrightness() {
        val report = QualityChecks.assess(sharpPage(), config)
        assertTrue(report.measures.meanLuma in 70.0..215.0)
    }

    @Test
    fun faintPrintHasLowSpread() {
        val faint = image(200, 200) { x, y -> 150 + (x * 7 + y * 13) % 51 }
        val report = QualityChecks.assess(faint, config)
        assertTrue("spread ${report.measures.contrastSpread}", report.measures.contrastSpread < config.minContrastSpread)
        assertTrue(QualityProblem.FAINT in report.problems)
    }

    @Test
    fun blackInkOnWhitePaperIsNotFaint() {
        assertEquals(205, QualityChecks.contrastSpread(sharpPage(), config))
    }

    @Test
    fun percentilesUseNearestRank() {
        val ramp = image(10, 10) { x, y -> y * 10 + x + 1 } // 1..100
        assertEquals(5, QualityChecks.percentile(ramp, 5.0))
        assertEquals(95, QualityChecks.percentile(ramp, 95.0))
        assertEquals(90, QualityChecks.contrastSpread(ramp, config))
    }

    @Test
    fun brightReflectionIsGlare() {
        val glare = image(800, 600) { x, y -> if (x in 300 until 420 && y in 200 until 320) 255 else 128 }
        val fraction = QualityChecks.glareFraction(glare, config)
        // 14,400 bright pixels minus two 4×8 corner blocks that are under 25% bright.
        assertEquals(14336.0 / 480000.0, fraction, 1e-12)
        assertTrue(QualityProblem.GLARE in QualityChecks.assess(glare, config).problems)
    }

    @Test
    fun scatteredSpecksAreNotGlare() {
        val specks = image(800, 600) { x, y -> if (x % 5 == 0 && y % 5 == 0) 255 else 200 }
        assertEquals(0.0, QualityChecks.glareFraction(specks, config), 0.0)
        assertFalse(QualityProblem.GLARE in QualityChecks.assess(specks, config).problems)
    }

    @Test
    fun thresholdsAreStrict() {
        assertTrue(QualityChecks.problems(QualityMeasures(60.0, 70.0, 80, 0.02), config).isEmpty())
        assertTrue(QualityChecks.problems(QualityMeasures(60.0, 215.0, 80, 0.02), config).isEmpty())
        assertEquals(
            listOf(QualityProblem.BLURRED, QualityProblem.TOO_DARK, QualityProblem.FAINT, QualityProblem.GLARE),
            QualityChecks.problems(QualityMeasures(59.9, 69.9, 79, 0.0201), config),
        )
        assertEquals(
            listOf(QualityProblem.WASHED_OUT),
            QualityChecks.problems(QualityMeasures(100.0, 215.1, 120, 0.0), config),
        )
    }

    @Test
    fun thresholdsComeFromTheConfigObject() {
        // Above the largest possible variance (a Laplacian value is at most 4 × 255 = 1020 in size).
        val strict = config.copy(minSharpness = 2_000_000.0)
        assertTrue(QualityProblem.BLURRED in QualityChecks.assess(sharpPage(), strict).problems)
    }

    @Test
    fun luminanceUsesBt601Weights() {
        val lum = Luma.fromArgb(
            intArrayOf(0xFFFFFFFF.toInt(), 0xFF000000.toInt(), 0xFFFF0000.toInt(), 0xFF00FF00.toInt(), 0xFF0000FF.toInt()),
        )
        assertEquals(listOf(255, 0, 76, 150, 29), lum.toList())
    }

    @Test
    fun qualityHeaderHasTheContractShape() {
        val header = QualityHeader.build(QualityMeasures(142.34, 171.0, 118, 0.004), 2250, 3000)
        assertEquals(
            """{"sharpness":142.3,"mean_luma":171.0,"contrast_spread":118,"glare_fraction":0.004,"width":2250,"height":3000}""",
            header,
        )
        assertEquals(
            """{"sharpness":0.0,"mean_luma":0.0,"contrast_spread":0,"glare_fraction":0.0,"width":1,"height":1}""",
            QualityHeader.build(QualityMeasures(Double.NaN, 0.0, 0, 0.0), 1, 1),
        )
    }

    @Test
    fun problemsAreDescribedInPlainWords() {
        assertEquals("page 3 looks blurred", QualityProblem.BLURRED.describe(3))
        assertEquals(QualityProblem.GLARE, QualityProblem.fromCode("glare"))
    }
}
