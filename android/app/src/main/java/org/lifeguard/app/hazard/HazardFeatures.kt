package org.lifeguard.app.hazard

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.ln1p
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * מאפייני חלון למנוע הסכנה v2 - העתק נאמן של hazard_features.py (אותו קוד באימון ובזמן ריצה ב-Windows).
 * נבדק מול פלטי Python (HazardParityTest, android/app/src/test/resources/hazard_parity.json).
 *
 * חלון = 1.5 שנ' ב-16kHz. שלב 1: ממוצע + מקסימום של embeddings של YAMNet. שלב 2: ציון שלב 1, הסתברויות YAMNet למחלקות
 * "מבלבלות" (מקס + ממוצע) ו-29 מאפיינים ספקטרליים (STFT ב-256/1024/4096, מעטפת, מחזוריות, אפנון, יציבות טון).
 */
object HazardFeatures {
    const val SR = 16000
    const val WINDOW_S = 1.5
    const val WINDOW = 24000
    const val N_SPECTRAL = 29
    private const val EPS = 1e-8
    private const val ENV_HOP = 160

    /** שלב 1: [ממוצע, מקסימום] לאורך המסגרות. ממוצע ב-float32 כמו numpy (צבירה לאורך ציר 0). */
    fun stage1Vector(emb: Array<FloatArray>): FloatArray {
        val d = emb[0].size
        val out = FloatArray(2 * d)
        for (j in 0 until d) {
            var s = 0f
            var mx = Float.NEGATIVE_INFINITY
            for (f in emb) { s += f[j]; if (f[j] > mx) mx = f[j] }
            out[j] = s / emb.size
            out[d + j] = mx
        }
        return out
    }

    /** שלב 2: [ציון שלב 1, מקס על המחלקות המבלבלות, ממוצע עליהן, 29 מאפיינים ספקטרליים]. */
    fun verifierVector(stage1Logit: Double, scores: Array<FloatArray>, curated: IntArray, x: FloatArray): FloatArray {
        val k = curated.size
        val out = FloatArray(1 + 2 * k + N_SPECTRAL)
        out[0] = stage1Logit.toFloat()
        for ((c, idx) in curated.withIndex()) {
            var s = 0f
            var mx = Float.NEGATIVE_INFINITY
            for (f in scores) { s += f[idx]; if (f[idx] > mx) mx = f[idx] }
            out[1 + c] = mx
            out[1 + k + c] = s / scores.size
        }
        val spec = spectralFeatures(x)
        for (i in spec.indices) out[1 + 2 * k + i] = spec[i].toFloat()
        return out
    }

    /** חלונות של 1.5 שנ' (האחרון מרופד / צמוד לסוף), כמו hazard_features.windows (בלי דגימה אקראית). */
    fun windows(x: FloatArray, hopS: Double = 0.75): List<Pair<Int, FloatArray>> {
        if (x.size <= WINDOW) return listOf(0 to x.copyOf(WINDOW))
        val hop = (hopS * SR).toInt()
        val starts = ArrayList<Int>()
        var s = 0
        while (s <= x.size - WINDOW) { starts += s; s += hop }
        if (starts.last() + WINDOW < x.size) starts += x.size - WINDOW
        return starts.map { st -> st to x.copyOfRange(st, st + WINDOW) }
    }

    // ------------------------------------------------------------------ מאפיינים ספקטרליים (29)
    fun spectralFeatures(input: FloatArray): DoubleArray {
        val n = input.size
        val mean = if (n > 0) input.sumOf { it.toDouble() }.div(n).toFloat() else 0f
        val x = FloatArray(n) { input[it] - mean }
        val nyq = SR / 2.0
        val out = ArrayList<Double>(N_SPECTRAL)
        var flux1024: DoubleArray? = null
        var s1024: Array<DoubleArray>? = null
        for (nFft in intArrayOf(256, 1024, 4096)) {
            val s = stftMag(x, nFft)
            if (nFft == 1024) s1024 = s
            val bins = s[0].size
            val step = nyq / (bins - 1)
            val freqs = DoubleArray(bins) { it * step }
            val frames = s.size
            val tot = DoubleArray(frames)
            val cent = DoubleArray(frames)
            val flat = DoubleArray(frames)
            var hi = 0.0; var lo = 0.0
            for (f in 0 until frames) {
                var t = 0.0; var c = 0.0; var lg = 0.0
                for (k in 0 until bins) {
                    val p = s[f][k] * s[f][k]
                    t += p; c += p * freqs[k]; lg += ln(p + EPS)
                    if (freqs[k] >= 4000) hi += p
                    if (freqs[k] < 300) lo += p
                }
                tot[f] = t + EPS
                cent[f] = c / tot[f] / nyq
                flat[f] = exp(lg / bins) / (t / bins + EPS)
            }
            val flux = if (frames > 1) DoubleArray(frames - 1) { f ->
                var acc = 0.0
                for (k in 0 until bins) acc += max(ln1p(s[f + 1][k]) - ln1p(s[f][k]), 0.0)
                acc
            } else DoubleArray(1)
            if (nFft == 1024) flux1024 = flux
            val totSum = tot.sum()
            val med = percentile(tot, 50.0)
            val loud = tot.indices.filter { tot[it] >= med }
            val fmax = flux.max()
            out += ln1p(fmax)
            out += fmax / (median(flux) + EPS)
            out += hi / totSum
            out += lo / totSum
            val lc = loud.map { cent[it] }
            out += lc.average()
            out += std(lc)
            out += loud.map { flat[it] }.average()
        }
        // זמן: חדות, קורטוזיס של המעטפת, דעיכה אחרי השיא
        val env = envelope(x)
        var sq = 0.0
        var peak = 0f
        for (v in x) { sq += (v * v).toDouble(); peak = max(peak, abs(v)) }
        val rms = sqrt((if (n > 0) sq / n else 0.0) + EPS)
        val crest = 20 * log10((peak + EPS) / rms)
        val em = env.average()
        val es = std(env.toList())
        val kurt = env.sumOf { val z = (it - em) / (es + EPS); z * z * z * z } / env.size
        var i = 0
        for (j in env.indices) if (env[j] > env[i]) i = j
        var below = -1
        for (j in i until env.size) if (env[j] < env[i] * 0.1) { below = j - i; break }
        val decay = if (below >= 0) below * ENV_HOP.toDouble() / SR else (env.size - i) * ENV_HOP.toDouble() / SR
        // מחזוריות אונסטים (מוזיקה / תופים) בטווח 0.25-1.0 שנ'
        var per = 0.0
        val fl = flux1024
        if (fl != null && fl.size > 8) {
            val fm = fl.average()
            val f = DoubleArray(fl.size) { fl[it] - fm }
            val ac = DoubleArray(f.size) { lag -> var a = 0.0; for (t in 0 until f.size - lag) a += f[t + lag] * f[t]; a }
            val a0 = ac[0] + EPS
            val loLag = (0.25 * SR / 256).toInt()
            val hiLag = min(ac.size - 1, (1.0 * SR / 256).toInt())
            if (hiLag > loLag) per = (loLag..hiLag).maxOf { ac[it] / a0 }
        }
        // אפנון 2-20Hz של המעטפת (צפצוף, סירנה)
        val emc = DoubleArray(env.size) { env[it] - em }
        val spec = powerSpectrum(emc)
        val v = 1.0 / (emc.size * (ENV_HOP.toDouble() / SR))
        var modNum = 0.0; var modDen = 0.0
        for (k in spec.indices) {
            val mf = k * v
            if (mf >= 2 && mf <= 20) modNum += spec[k]
            if (mf > 0.5) modDen += spec[k]
        }
        val mod = modNum / (modDen + EPS)
        // יציבות טון (צופר) - מתוך ה-STFT של 1024
        val s = s1024!!
        val bins = s[0].size
        val step = nyq / (bins - 1)
        val band = (0 until bins).filter { val fr = it * step; fr >= 300 && fr <= 3000 }
        val eFr = DoubleArray(s.size) { f -> s[f].sumOf { it * it } }
        val thr60 = percentile(eFr, 60.0)
        val loud = eFr.indices.filter { eFr[it] >= thr60 }
        var pitchStd = 0.0
        var tonal = 0.0
        if (loud.isNotEmpty()) {
            val peaks = loud.map { f ->
                var best = band[0]
                for (b in band) if (s[f][b] > s[f][best]) best = b
                best * step
            }
            pitchStd = std(peaks) / 1000.0
            tonal = loud.map { f ->
                val sorted = band.map { s[f][it] * s[f][it] }.sorted()
                (sorted[sorted.size - 1] + sorted[sorted.size - 2]) / (sorted.sum() + EPS)
            }.average()
        }
        val level = 20 * log10(rms)
        out += listOf(crest, ln1p(kurt), decay, per, mod, pitchStd, tonal, level / 60.0)
        check(out.size == N_SPECTRAL)
        return out.toDoubleArray()
    }

    /** RMS נע בחלון של 160 דגימות (np.convolve mode="same"), נדגם כל 160 - כמו ב-Python. */
    private fun envelope(x: FloatArray): DoubleArray {
        val n = x.size
        val sq = DoubleArray(n) { (x[it] * x[it]).toDouble() }
        val m = (n + ENV_HOP - 1) / ENV_HOP
        return DoubleArray(m) { j ->
            val k = j * ENV_HOP
            val a = max(0, k - ENV_HOP / 2)
            val b = min(n - 1, k + ENV_HOP / 2 - 1)
            var acc = 0.0
            for (t in a..b) acc += sq[t]
            sqrt(acc / ENV_HOP + EPS)
        }
    }

    private val hannCache = HashMap<Int, FloatArray>()
    private val fftCache = HashMap<Int, Fft>()

    @Synchronized private fun hann(n: Int) = hannCache.getOrPut(n) {
        FloatArray(n) { (0.5 - 0.5 * cos(2.0 * PI * it / (n - 1))).toFloat() }
    }

    @Synchronized private fun fft(n: Int) = fftCache.getOrPut(n) { Fft(n) }

    /** |rfft| של מסגרות עם חלון Hann, קפיצה n/4 (קלט קצר מרופד באפסים). */
    private fun stftMag(x0: FloatArray, nFft: Int): Array<DoubleArray> {
        val x = if (x0.size < nFft) x0.copyOf(nFft) else x0
        val hop = nFft / 4
        val frames = 1 + (x.size - nFft) / hop
        val w = hann(nFft)
        val f = fft(nFft)
        val re = DoubleArray(nFft)
        val im = DoubleArray(nFft)
        return Array(frames) { fr ->
            val off = fr * hop
            for (i in 0 until nFft) { re[i] = (x[off + i] * w[i]).toDouble(); im[i] = 0.0 }
            f.transform(re, im)
            DoubleArray(nFft / 2 + 1) { k -> sqrt(re[k] * re[k] + im[k] * im[k]) }
        }
    }

    /** |rfft(x)|^2 באורך כלשהו (DFT ישיר - המעטפת קצרה, ~150 נקודות). */
    private fun powerSpectrum(x: DoubleArray): DoubleArray {
        val n = x.size
        return DoubleArray(n / 2 + 1) { k ->
            var re = 0.0; var im = 0.0
            for (t in 0 until n) {
                val a = 2.0 * PI * ((k.toLong() * t) % n) / n
                re += x[t] * cos(a); im -= x[t] * sin(a)
            }
            re * re + im * im
        }
    }

    /** np.percentile (method="linear"), כולל ה-lerp הסימטרי של numpy. */
    fun percentile(a: DoubleArray, q: Double): Double {
        val s = a.sortedArray()
        val idx = q / 100.0 * (s.size - 1)
        val lo = floor(idx).toInt()
        val hi = min(lo + 1, s.size - 1)
        val t = idx - lo
        val diff = s[hi] - s[lo]
        return if (t >= 0.5) s[hi] - diff * (1 - t) else s[lo] + diff * t
    }

    private fun median(a: DoubleArray): Double {
        val s = a.sortedArray()
        val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2.0
    }

    private fun std(a: List<Double>): Double {
        if (a.isEmpty()) return Double.NaN
        val m = a.average()
        return sqrt(a.sumOf { (it - m) * (it - m) } / a.size)
    }

    private class Fft(val n: Int) {
        private val levels = Integer.numberOfTrailingZeros(n)
        private val cosT = DoubleArray(n / 2) { cos(2 * PI * it / n) }
        private val sinT = DoubleArray(n / 2) { sin(2 * PI * it / n) }
        private val rev = IntArray(n) { Integer.reverse(it) ushr (32 - levels) }

        init { require(n > 1 && Integer.bitCount(n) == 1) { "FFT size must be a power of two" } }

        fun transform(re: DoubleArray, im: DoubleArray) {
            for (i in 0 until n) {
                val j = rev[i]
                if (j > i) {
                    var t = re[i]; re[i] = re[j]; re[j] = t
                    t = im[i]; im[i] = im[j]; im[j] = t
                }
            }
            var size = 2
            while (size <= n) {
                val half = size / 2
                val step = n / size
                var i = 0
                while (i < n) {
                    var k = 0
                    for (j in i until i + half) {
                        val l = j + half
                        val tre = re[l] * cosT[k] + im[l] * sinT[k]
                        val tim = im[l] * cosT[k] - re[l] * sinT[k]
                        re[l] = re[j] - tre; im[l] = im[j] - tim
                        re[j] += tre; im[j] += tim
                        k += step
                    }
                    i += size
                }
                size *= 2
            }
        }
    }
}
