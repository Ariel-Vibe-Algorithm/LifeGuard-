package org.lifeguard.app.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.text.StaticLayout
import android.text.TextPaint
import android.util.AttributeSet
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo
import android.view.animation.PathInterpolator
import kotlin.math.min

/**
 * כפתור "לחץ והחזק": מופעל רק אחרי החזקה רציפה של holdMillis (ברירת מחדל 2 שנ') - מונע הפעלה בטעות בכיס.
 * עיגול עם הילה עדינה וטבעת התקדמות. נגישות: לחיצה ארוכה ב-TalkBack מפעילה.
 *
 * תזמון: ההפעלה מתוזמנת ב-Handler (postDelayed) לפי שעון המערכת - בדיוק holdMillis, בלי קשר להגדרת
 * "קנה מידה של אנימציות" במכשיר. (ValueAnimator מושפע ממנה: באנימציות כבויות הוא מסתיים מיד - כלומר נגיעה אחת
 * הייתה שולחת SOS - ובקנה מידה 10x ההחזקה הייתה 20 שנ'.) הציור של הטבעת הוא רק תצוגה של אותו שעון.
 */
class HoldButton @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null) : View(ctx, attrs) {
    var holdMillis = 2000L
    var fillColor = 0xFFD70015.toInt()
        set(v) { field = v; invalidate() }
    var label: CharSequence = "SOS"
        set(v) { field = v; contentDescription = v; invalidate() }
    var textSizePx = 22f * resources.displayMetrics.scaledDensity
        set(v) { field = v; invalidate() }
    /** הילה בצבע הכפתור סביב העיגול (ב-SOS הגדול); בכפתור הדיסקרטי אפשר לכבות */
    var showHalo = true
        set(v) { field = v; invalidate() }
    var onTriggered: (() -> Unit)? = null

    /** uptime של תחילת ההחזקה; 0 = לא מחזיקים */
    private var pressStart = 0L
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val halo = Paint(Paint.ANTI_ALIAS_FLAG)
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; color = 0x4DFFFFFF }
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; color = 0xFFFFFFFF.toInt(); strokeCap = Paint.Cap.ROUND
    }
    private val text = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFFFFFF.toInt(); isFakeBoldText = true }
    private val oval = RectF()

    private val fire = Runnable { if (pressStart != 0L) trigger() }
    private val frame = object : Runnable {
        override fun run() {
            if (pressStart == 0L) return
            invalidate()
            postOnAnimation(this)
        }
    }

    init {
        isClickable = true
        isFocusable = true
        isLongClickable = true
    }

    private fun progress(): Float =
        if (pressStart == 0L) 0f else ((SystemClock.uptimeMillis() - pressStart).toFloat() / holdMillis).coerceIn(0f, 1f)

    override fun onDraw(c: Canvas) {
        val size = min(width, height).toFloat()
        val cx = width / 2f; val cy = height / 2f
        val outer = size / 2f - 2f
        val haloW = if (showHalo) outer * 0.09f else 0f
        val r = outer - haloW
        val alpha = if (isEnabled) 255 else 115
        if (haloW > 0f) {
            halo.color = fillColor; halo.alpha = alpha * 40 / 255
            c.drawCircle(cx, cy, outer, halo)
        }
        fill.color = fillColor; fill.alpha = alpha
        c.drawCircle(cx, cy, r, fill)
        val p = progress()
        if (p > 0f) {
            ring.strokeWidth = size * 0.05f
            track.strokeWidth = ring.strokeWidth
            val inset = ring.strokeWidth / 2 + r * 0.07f
            oval.set(cx - r + inset, cy - r + inset, cx + r - inset, cy + r - inset)
            c.drawOval(oval, track)
            c.drawArc(oval, -90f, 360f * p, false, ring)
        }
        text.textSize = textSizePx
        val w = (r * 1.45f).toInt().coerceAtLeast(1)
        val layout = StaticLayout.Builder.obtain(label, 0, label.length, text, w)
            .setAlignment(android.text.Layout.Alignment.ALIGN_CENTER).setLineSpacing(0f, 1.05f).build()
        c.save()
        c.translate(cx - w / 2f, cy - layout.height / 2f)
        layout.draw(c)
        c.restore()
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (!isEnabled) return false
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> { startHold(); return true }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { cancelHold(); if (e.actionMasked == MotionEvent.ACTION_UP) performClick() }
            MotionEvent.ACTION_MOVE -> if (e.x < 0 || e.y < 0 || e.x > width || e.y > height) cancelHold()
        }
        return true
    }

    override fun performClick(): Boolean { super.performClick(); return true }

    /** נגישות: לחיצה ארוכה (TalkBack: הקשה כפולה והחזקה) */
    override fun performLongClick(): Boolean {
        if (isAccessibilityFocused) { trigger(); return true }
        return super.performLongClick()
    }

    override fun onInitializeAccessibilityNodeInfo(info: AccessibilityNodeInfo) {
        super.onInitializeAccessibilityNodeInfo(info)
        info.addAction(AccessibilityNodeInfo.AccessibilityAction(AccessibilityNodeInfo.ACTION_LONG_CLICK, label))
    }

    override fun onDetachedFromWindow() {
        cancelHold()
        super.onDetachedFromWindow()
    }

    private fun startHold() {
        removeCallbacks(fire)
        pressStart = SystemClock.uptimeMillis()
        performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
        postDelayed(fire, holdMillis)
        postOnAnimation(frame)
        press(true)
    }

    private fun cancelHold() {
        removeCallbacks(fire); removeCallbacks(frame)
        pressStart = 0L
        invalidate()
        press(false)
    }

    private fun trigger() {
        removeCallbacks(fire); removeCallbacks(frame)
        pressStart = 0L
        invalidate()
        performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
        press(false)
        onTriggered?.invoke()
    }

    /** משוב לחיצה: הכפתור "נלחץ" ל-96% (רק כשהאנימציות במכשיר פעילות). תצוגה בלבד - לא משפיע על התזמון. */
    private fun press(down: Boolean) {
        animate().cancel()
        if (!Ui.motionEnabled()) { scaleX = 1f; scaleY = 1f; return }
        val s = if (down) 0.96f else 1f
        animate().scaleX(s).scaleY(s).setDuration(if (down) 120L else 200L).setInterpolator(EASE).start()
    }

    private companion object {
        val EASE = PathInterpolator(0.2f, 0.8f, 0.2f, 1f)
    }
}
