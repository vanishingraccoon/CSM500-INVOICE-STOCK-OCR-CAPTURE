package com.invoicecapture.domain.quality

import com.invoicecapture.domain.util.Decimals

/** The optional `X-Quality` upload header (contract §2). */
object QualityHeader {
    /**
     * For example `{"sharpness":142.3,"mean_luma":171.0,"contrast_spread":118,
     * "glare_fraction":0.004,"width":2250,"height":3000}`; width and height are the uploaded JPEG's.
     */
    fun build(measures: QualityMeasures, width: Int, height: Int): String =
        "{" +
            "\"sharpness\":${Decimals.format(measures.sharpness, 1)}," +
            "\"mean_luma\":${Decimals.format(measures.meanLuma, 1)}," +
            "\"contrast_spread\":${measures.contrastSpread}," +
            "\"glare_fraction\":${Decimals.format(measures.glareFraction, 4, minDecimals = 1)}," +
            "\"width\":$width," +
            "\"height\":$height" +
            "}"
}
