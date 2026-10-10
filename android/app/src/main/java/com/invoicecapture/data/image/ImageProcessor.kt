package com.invoicecapture.data.image

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import com.invoicecapture.domain.quality.ImageSizing
import com.invoicecapture.domain.quality.Luma
import com.invoicecapture.domain.quality.LumaImage
import com.invoicecapture.domain.quality.QualityChecks
import com.invoicecapture.domain.quality.QualityConfig
import com.invoicecapture.domain.quality.QualityReport
import com.invoicecapture.domain.util.Hashing
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

class UnreadableImageException(message: String) : IOException(message)

/**
 * Turns one scanner JPEG into the upload copy and its quality report (contract §6):
 * EXIF orientation applied, long side at most 3000 px, JPEG quality 90; the checks run on an
 * 800 px grayscale copy. The photo is kept as taken: no cleaning, no filters.
 */
class ImageProcessor(private val config: QualityConfig = QualityConfig.DEFAULT) {

    data class Prepared(
        val sha256: String,
        val bytes: Long,
        val width: Int,
        val height: Int,
        val report: QualityReport,
    )

    /** Reads [source], writes the upload copy atomically to [target]. Runs on a background thread. */
    fun prepare(source: File, target: File): Prepared {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(source.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw UnreadableImageException("not a picture")

        val orientation = try {
            ExifInterface(source.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        } catch (e: IOException) {
            ExifInterface.ORIENTATION_NORMAL
        }

        val options = BitmapFactory.Options().apply {
            inSampleSize = ImageSizing.sampleSizeFor(bounds.outWidth, bounds.outHeight, config.uploadLongSide)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val decoded = BitmapFactory.decodeFile(source.path, options)
            ?: throw UnreadableImageException("the photo could not be decoded")
        val upload = orientAndFit(decoded, orientation)
        try {
            val tmp = File(target.parentFile, target.name + ".part")
            FileOutputStream(tmp).use { out ->
                if (!upload.compress(Bitmap.CompressFormat.JPEG, config.uploadJpegQuality, out)) {
                    throw IOException("JPEG encoding failed")
                }
                out.flush()
                out.fd.sync()
            }
            val report = analyse(upload)
            Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            return Prepared(
                sha256 = Hashing.sha256Hex(target),
                bytes = target.length(),
                width = upload.width,
                height = upload.height,
                report = report,
            )
        } finally {
            upload.recycle()
        }
    }

    private fun analyse(bitmap: Bitmap): QualityReport {
        val small = downscale(bitmap, config.analysisLongSide)
        try {
            val w = small.width
            val h = small.height
            val argb = IntArray(w * h)
            small.getPixels(argb, 0, w, 0, 0, w, h)
            return QualityChecks.assess(LumaImage(w, h, Luma.fromArgb(argb)), config)
        } finally {
            if (small !== bitmap) small.recycle()
        }
    }

    /** Halves with filtering until near the target, then one filtered scale: cheap area averaging. */
    private fun downscale(src: Bitmap, maxLongSide: Int): Bitmap {
        var current = src
        while (maxOf(current.width, current.height) / 2 >= maxLongSide) {
            val next = Bitmap.createScaledBitmap(current, maxOf(1, current.width / 2), maxOf(1, current.height / 2), true)
            if (current !== src) current.recycle()
            current = next
        }
        val size = ImageSizing.fitLongSide(current.width, current.height, maxLongSide)
        if (size.width == current.width && size.height == current.height) return current
        val out = Bitmap.createScaledBitmap(current, size.width, size.height, true)
        if (current !== src) current.recycle()
        return out
    }

    /** One pass: scale to the upload size, rotate and mirror upright. Frees [decoded] if a copy was made. */
    private fun orientAndFit(decoded: Bitmap, orientation: Int): Bitmap {
        val transform = ImageSizing.exifTransform(orientation)
        val upright = ImageSizing.orientedSize(decoded.width, decoded.height, orientation)
        val target = ImageSizing.fitLongSide(upright.width, upright.height, config.uploadLongSide)
        val scale = target.width.toFloat() / upright.width
        if (transform.rotationDegrees == 0 && !transform.mirror && scale == 1f) return decoded
        val matrix = Matrix().apply {
            postScale(scale, scale)
            postRotate(transform.rotationDegrees.toFloat())
            if (transform.mirror) postScale(-1f, 1f)
        }
        val out = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true)
        if (out !== decoded) decoded.recycle()
        return out
    }
}
