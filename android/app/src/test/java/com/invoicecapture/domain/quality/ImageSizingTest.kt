package com.invoicecapture.domain.quality

import org.junit.Assert.assertEquals
import org.junit.Test

class ImageSizingTest {

    @Test
    fun landscapeIsFittedToTheLongSide() {
        assertEquals(PixelSize(3000, 2250), ImageSizing.fitLongSide(4000, 3000, 3000))
    }

    @Test
    fun portraitIsFittedToTheLongSide() {
        assertEquals(PixelSize(2250, 3000), ImageSizing.fitLongSide(3024, 4032, 3000))
        assertEquals(PixelSize(600, 800), ImageSizing.fitLongSide(2250, 3000, 800))
    }

    @Test
    fun smallImagesAreNeverUpscaled() {
        assertEquals(PixelSize(2000, 1500), ImageSizing.fitLongSide(2000, 1500, 3000))
        assertEquals(PixelSize(3000, 3000), ImageSizing.fitLongSide(3000, 3000, 3000))
    }

    @Test
    fun sampleSizeKeepsEnoughPixels() {
        assertEquals(1, ImageSizing.sampleSizeFor(4000, 3000, 3000))
        assertEquals(2, ImageSizing.sampleSizeFor(8000, 6000, 3000))
        assertEquals(8, ImageSizing.sampleSizeFor(12000, 9000, 800))
    }

    @Test
    fun exifOrientationsMapToRotationAndMirror() {
        assertEquals(ExifTransform(0, false), ImageSizing.exifTransform(1))
        assertEquals(ExifTransform(0, true), ImageSizing.exifTransform(2))
        assertEquals(ExifTransform(180, false), ImageSizing.exifTransform(3))
        assertEquals(ExifTransform(180, true), ImageSizing.exifTransform(4))
        assertEquals(ExifTransform(90, true), ImageSizing.exifTransform(5))
        assertEquals(ExifTransform(90, false), ImageSizing.exifTransform(6))
        assertEquals(ExifTransform(270, true), ImageSizing.exifTransform(7))
        assertEquals(ExifTransform(270, false), ImageSizing.exifTransform(8))
        assertEquals(ExifTransform(0, false), ImageSizing.exifTransform(0))
    }

    @Test
    fun quarterTurnsSwapWidthAndHeight() {
        assertEquals(PixelSize(3000, 4000), ImageSizing.orientedSize(4000, 3000, 6))
        assertEquals(PixelSize(4000, 3000), ImageSizing.orientedSize(4000, 3000, 3))
    }
}
