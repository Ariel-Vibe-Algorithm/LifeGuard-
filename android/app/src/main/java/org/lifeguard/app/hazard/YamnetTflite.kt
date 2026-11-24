package org.lifeguard.app.hazard

import android.content.Context
import org.tensorflow.lite.Interpreter
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel

/**
 * YAMNet במכשיר (LiteRT / TensorFlow Lite) - אותו מודל כמו ב-Windows (yamnet_float_embeddings.tflite):
 * קלט waveform 16kHz float32 באורך משתנה, פלטים scores[N,521] ו-embeddings[N,1024] (ופלט spectrogram שלא בשימוש).
 * רץ רק במכשיר (לא בבדיקות JVM). לא שומר ולא שולח אודיו.
 */
class YamnetTflite(ctx: Context, asset: String) : YamnetRunner, AutoCloseable {
    private val interpreter: Interpreter
    private val scoresIdx: Int
    private val embIdx: Int
    private var allocatedFor = -1

    init {
        // המודל נשמר לא דחוס ב-APK (noCompress "tflite") -> memory map בלי להעתיק 16MB לזיכרון
        val buf = ctx.assets.openFd(asset).use { fd ->
            FileInputStream(fd.fileDescriptor).channel.use { ch ->
                ch.map(FileChannel.MapMode.READ_ONLY, fd.startOffset, fd.declaredLength)
            }
        }
        interpreter = Interpreter(buf, Interpreter.Options().setNumThreads(2))
        var s = -1
        var e = -1
        for (i in 0 until interpreter.outputTensorCount) {
            when (interpreter.getOutputTensor(i).shapeSignature().last()) {
                NUM_CLASSES -> s = i
                EMB_DIM -> e = i
            }
        }
        check(s >= 0 && e >= 0) { "unexpected YAMNet outputs" }
        scoresIdx = s
        embIdx = e
    }

    @Synchronized
    override fun run(x16: FloatArray): YamnetOutput {
        if (allocatedFor != x16.size) {
            interpreter.resizeInput(0, intArrayOf(x16.size))
            interpreter.allocateTensors()
            allocatedFor = x16.size
        }
        val input = ByteBuffer.allocateDirect(x16.size * 4).order(ByteOrder.nativeOrder())
        input.asFloatBuffer().put(x16)
        var n = AudioIntel.nFrames(x16.size)
        repeat(2) { attempt ->
            val sc = ByteBuffer.allocateDirect(n * NUM_CLASSES * 4).order(ByteOrder.nativeOrder())
            val em = ByteBuffer.allocateDirect(n * EMB_DIM * 4).order(ByteOrder.nativeOrder())
            input.rewind()
            try {
                interpreter.runForMultipleInputsOutputs(arrayOf<Any>(input), mapOf<Int, Any>(scoresIdx to sc, embIdx to em))
                val actual = interpreter.getOutputTensor(embIdx).shape()[0]
                if (actual == n) return YamnetOutput(rows(sc, n, NUM_CLASSES), rows(em, n, EMB_DIM))
                n = actual
            } catch (e: RuntimeException) {
                // מספר המסגרות שונה מהנוסחה -> לפי הצורה האמיתית, ניסיון אחד נוסף
                if (attempt == 1) throw e
                n = interpreter.getOutputTensor(embIdx).shape()[0]
            }
        }
        error("YAMNet output shape mismatch")
    }

    private fun rows(b: ByteBuffer, n: Int, d: Int): Array<FloatArray> {
        b.rewind()
        val fb = b.asFloatBuffer()
        return Array(n) { FloatArray(d).also { fb.get(it) } }
    }

    override fun close() = interpreter.close()

    companion object {
        const val NUM_CLASSES = 521
        const val EMB_DIM = 1024
    }
}
