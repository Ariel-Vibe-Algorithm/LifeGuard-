package org.lifeguard.app.hazard

import org.json.JSONArray
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * המודלים של מנוע הסכנה הקולי, כפי שיוצאו מהמאגר ע"י tools/build_android_hazard_assets.py
 * (assets/hazard/hazard_models.json + hazard_models.bin). שום מספר לא מוקלד כאן - הכל מקובצי ה-npz.
 */

/** ראש לינארי: ((x - mean) / std) · w + b - בדיוק כמו audio_intel / LogisticModel.decision_function. */
class LinearHead(val mean: FloatArray, val std: FloatArray, val w: FloatArray, val b: Double) {
    init { require(mean.size == w.size && std.size == w.size) { "head dims mismatch" } }
    val dim get() = w.size

    /** הנרמול ב-float32 (כמו numpy), הצבירה ב-double. */
    fun logit(x: FloatArray, offset: Int = 0): Double {
        var acc = 0.0
        for (i in w.indices) {
            val z = (x[offset + i] - mean[i]) / std[i]
            acc += z.toDouble() * w[i].toDouble()
        }
        return acc + b
    }
}

class V1Category(
    val name: String,
    val labelHe: String,
    val severityPrior: String,
    /** "head" (ראש מאומן על embeddings) או "zero_shot" (ציון YAMNet המקורי) */
    val champion: String,
    val yamIdx: IntArray,
    val threshold: Double,
    val plattA: Double,
    val plattB: Double,
    /** (סף, התראות שווא לשעה) - עולה בסף */
    val faTable: List<Pair<Double, Double>>,
    val head: LinearHead?,
)

class UnvalidatedCategory(val name: String, val labelHe: String, val yamIdx: IntArray, val threshold: Double, val note: String)

class V2Category(
    val name: String,
    val t1: Double,
    val t2: Double,
    val twoStage: Boolean,
    val plattA: Double,
    val plattB: Double,
    val s1: LinearHead,
    val s2: LinearHead?,
)

class DistressModel(val context: Boolean, val smooth: Int, val thresholdLogit: Double, val head: LinearHead, val variant: String?)

/** כלל "רצף צעקות" (models/shout_sequence.json): k חלונות "כמעט-צעקה" בתוך span_s*2 שניות. */
class ShoutRule(val version: String?, val k: Int, val tLow: Double, val sLow: Double, val spanS: Double)

class HazardModels(
    val engine: String,
    val v1Version: String,
    val frameHopS: Double,
    val v1: List<V1Category>,
    val unvalidated: List<UnvalidatedCategory>,
    val v2Version: String?,
    val curatedIdx: IntArray,
    val v2: List<V2Category>,
    val distress: DistressModel,
    val shoutRule: ShoutRule?,
    val nearThresholdFraction: Double,
    val digitalSilencePeak: Double,
    val clipLevel: Double,
    val clipRatioDegraded: Double,
    val minSeconds: Double,
    val yamnetAsset: String,
    val sources: Map<String, String>,
) {
    val v2ByName: Map<String, V2Category> = v2.associateBy { it.name }

    companion object {
        fun parse(json: String, bin: ByteArray): HazardModels {
            val m = JSONObject(json)
            require(m.getString("format") == "lifeguard_hazard_assets_v1") { "unknown hazard asset format" }
            val fb = ByteBuffer.wrap(bin).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
            require(fb.capacity() == m.getInt("floats")) { "hazard_models.bin size mismatch" }
            fun arr(ref: JSONArray): FloatArray {
                val off = ref.getInt(0); val n = ref.getInt(1)
                return FloatArray(n).also { out -> fb.position(off); fb.get(out) }
            }
            fun head(o: JSONObject) = LinearHead(arr(o.getJSONArray("mean")), arr(o.getJSONArray("std")),
                arr(o.getJSONArray("w")), o.getDouble("b"))
            fun ints(a: JSONArray) = IntArray(a.length()) { a.getInt(it) }
            fun names(a: JSONArray) = (0 until a.length()).map { a.getString(it) }

            val ai = m.getJSONObject("audio_intel")
            val j1 = m.getJSONObject("v1")
            val c1 = j1.getJSONObject("categories")
            val v1 = names(j1.getJSONArray("order")).map { name ->
                val c = c1.getJSONObject(name)
                val platt = c.getJSONArray("platt")
                val fa = c.getJSONArray("fa_table")
                V1Category(name, c.getString("label_he"), c.getString("severity_prior"), c.getString("champion"),
                    ints(c.getJSONArray("yam_idx")), c.getDouble("threshold"), platt.getDouble(0), platt.getDouble(1),
                    (0 until fa.length()).map { fa.getJSONArray(it).let { r -> r.getDouble(0) to r.getDouble(1) } },
                    c.optJSONObject("head")?.let { head(it) })
            }
            val uj = j1.optJSONObject("unvalidated") ?: JSONObject()
            val unval = names(j1.optJSONArray("unvalidated_order") ?: JSONArray()).map { name ->
                val u = uj.getJSONObject(name)
                UnvalidatedCategory(name, u.getString("label_he"), ints(u.getJSONArray("yam_idx")), u.getDouble("threshold"),
                    u.optString("note"))
            }
            val j2 = if (m.isNull("v2")) null else m.getJSONObject("v2")
            val v2 = j2?.let { v ->
                val c2 = v.getJSONObject("categories")
                names(v.getJSONArray("order")).map { name ->
                    val c = c2.getJSONObject(name)
                    val platt = c.getJSONArray("platt")
                    V2Category(name, c.getDouble("t1"), c.getDouble("t2"), c.getBoolean("two_stage"), platt.getDouble(0),
                        platt.getDouble(1), head(c.getJSONObject("s1")), c.optJSONObject("s2")?.let { head(it) })
                }
            } ?: emptyList()
            val d = m.getJSONObject("distress")
            val seq = m.optJSONObject("shout_sequence")?.takeIf { s -> listOf("k", "t_low", "s_low", "span_s").all { s.has(it) } }
            val src = m.optJSONObject("sources") ?: JSONObject()
            return HazardModels(
                engine = ai.getString("engine"),
                v1Version = j1.getString("version"),
                frameHopS = j1.getDouble("frame_hop_s"),
                v1 = v1,
                unvalidated = unval,
                v2Version = j2?.getString("version"),
                curatedIdx = j2?.let { ints(it.getJSONArray("curated_idx")) } ?: IntArray(0),
                v2 = v2,
                distress = DistressModel(d.getBoolean("context"), d.getInt("smooth"), d.getDouble("threshold_logit"),
                    head(d.getJSONObject("head")), d.optString("variant").ifEmpty { null }),
                shoutRule = seq?.let { ShoutRule(it.optString("version").ifEmpty { null }, it.getInt("k"), it.getDouble("t_low"),
                    it.getDouble("s_low"), it.getDouble("span_s")) },
                nearThresholdFraction = ai.getDouble("near_threshold_fraction"),
                digitalSilencePeak = ai.getDouble("digital_silence_peak"),
                clipLevel = ai.getDouble("clip_level"),
                clipRatioDegraded = ai.getDouble("clip_ratio_degraded"),
                minSeconds = ai.getDouble("min_seconds"),
                yamnetAsset = m.getJSONObject("yamnet").getString("asset"),
                sources = src.keys().asSequence().associateWith { src.getString(it) },
            )
        }
    }
}
