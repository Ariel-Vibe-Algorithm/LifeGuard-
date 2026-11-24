package org.lifeguard.wear

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.GradientDrawable
import android.os.SystemClock
import android.text.StaticLayout
import android.text.TextPaint
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.lifeguard.wear.logic.HoldToTrigger
import org.lifeguard.wear.logic.SosDelivery
import kotlin.math.min

/**
 * Watch palette = the shared tokens (design/tokens.json, color.dark) on a true-black OLED background.
 * Filled buttons use the fixed "fill" colours with white text (>= 4.5:1); status text uses the dark-mode tokens.
 */
object WatchColors {
    const val BG = 0xFF000000.toInt()
    const val LABEL = 0xFFEEF2F8.toInt()
    const val LABEL_SECONDARY = 0xFFB2BDD0.toInt()
    const val SOS_FILL = 0xFFD70015.toInt()          // lg_fill_sos (same red as the phone)
    const val SOS_DISABLED = 0xFF3A1416.toInt()      // lg_sos_tint (dark)
    const val SOS_TINT_TEXT = 0xFFFFE9EA.toInt()     // lg_sos_tint (light), text on the dark-red fall screen
    const val SUCCESS_FILL = 0xFF1E7F3B.toInt()      // lg_fill_success
    const val NAVY_FILL = 0xFF132E5C.toInt()         // lg_brand_navy_2 (light)
    const val NEUTRAL_FILL = 0xFF26375A.toInt()      // lg_separator (dark)
    const val SURFACE_FILL = 0xFF1E2E48.toInt()      // lg_fill (dark)
    const val OK = 0xFF4ADE80.toInt()                // lg_success (dark)
    const val WARN = 0xFFFFB547.toInt()              // lg_warning (dark)
    const val ERROR = 0xFFFF4F45.toInt()             // lg_sos (dark)
    const val FALL_BG = 0xFF3A1416.toInt()           // lg_sos_tint (dark)
}

fun Context.dp(v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics)

/**
 * כפתור SOS עגול גדול בהחזקה (HoldToTrigger): טבעת התקדמות + רטט קצר בכל רבע; רק אחרי 2 שנ' רצופות -> onTriggered.
 * TalkBack: לחיצה ארוכה מפעילה (ועדיין יש אחריה חלון ביטול).
 */
class HoldSosView(ctx: Context) : View(ctx) {
    val hold = HoldToTrigger()
    var onTriggered: (() -> Unit)? = null
    var label: CharSequence = "SOS"
        set(v) { field = v; contentDescription = v; invalidate() }

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = WatchColors.SOS_FILL }
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; color = Color.WHITE }
    private val text = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; isFakeBoldText = true }
    private val oval = RectF()

    private val frame = object : Runnable {
        override fun run() {
            handle(hold.tick(SystemClock.uptimeMillis()))
            invalidate()
            if (hold.holding) postOnAnimation(this)
        }
    }

    init {
        isClickable = true
        isFocusable = true
        isLongClickable = true
        contentDescription = label
    }

    private fun handle(events: List<HoldToTrigger.Event>) {
        for (e in events) when (e) {
            is HoldToTrigger.Event.Haptic -> Haptics.tick(context)
            HoldToTrigger.Event.Triggered -> onTriggered?.invoke()
            HoldToTrigger.Event.Released -> {}
        }
    }

    fun resetHold() { hold.reset(); invalidate() }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (!isEnabled) return false
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                parent?.requestDisallowInterceptTouchEvent(true)     // לא לגלול/להחליק בזמן ההחזקה
                hold.press(SystemClock.uptimeMillis())
                Haptics.tick(context)
                postOnAnimation(frame)
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                handle(hold.release(SystemClock.uptimeMillis()))
                removeCallbacks(frame)
                if (!hold.triggered) invalidate()
            }
        }
        return true
    }

    override fun onInitializeAccessibilityNodeInfo(info: AccessibilityNodeInfo) {
        super.onInitializeAccessibilityNodeInfo(info)
        info.addAction(AccessibilityNodeInfo.AccessibilityAction(AccessibilityNodeInfo.ACTION_LONG_CLICK, label))
    }

    override fun performLongClick(): Boolean {
        if (!hold.triggered) { hold.reset(); onTriggered?.invoke() }
        return true
    }

    override fun onDraw(c: Canvas) {
        val size = min(width, height).toFloat()
        val cx = width / 2f; val cy = height / 2f; val r = size / 2f - 4f
        fill.color = if (isEnabled) WatchColors.SOS_FILL else WatchColors.SOS_DISABLED
        c.drawCircle(cx, cy, r, fill)
        val p = hold.progress(SystemClock.uptimeMillis())
        if (p > 0f) {
            ring.strokeWidth = size * 0.07f
            val inset = ring.strokeWidth / 2 + 4f
            oval.set(cx - r + inset, cy - r + inset, cx + r - inset, cy + r - inset)
            c.drawArc(oval, -90f, 360f * p, false, ring)
        }
        text.textSize = size * 0.13f
        val w = (r * 1.4f).toInt().coerceAtLeast(1)
        val layout = StaticLayout.Builder.obtain(label, 0, label.length, text, w)
            .setAlignment(android.text.Layout.Alignment.ALIGN_CENTER).build()
        c.save()
        c.translate(cx - w / 2f, cy - layout.height / 2f)
        layout.draw(c)
        c.restore()
    }
}

/** אזור מצב המסירה + "התקשר/י 100" / "101" / "נסה/י שוב" - משותף למסך הראשי ולמסך הנפילה */
class StatusPanel(private val ctx: Context, parent: LinearLayout) {
    val title = text(16f, bold = true)
    val detail = text(13f)
    private val call100 = button(ctx.getString(R.string.call_100), WatchColors.SOS_FILL) { Dialer.dial(ctx, "100") }
    private val call101 = button(ctx.getString(R.string.call_101), WatchColors.NAVY_FILL) { Dialer.dial(ctx, "101") }
    private val retry = button(ctx.getString(R.string.retry), WatchColors.SURFACE_FILL) { WatchController.retry() }

    init {
        for (v in listOf(title, detail, call100, call101, retry)) parent.addView(v)
        render(SosDelivery.View("", "", false, false, SosDelivery.Tone.PROGRESS))
    }

    fun render(v: SosDelivery.View) {
        title.text = v.title
        title.visibility = if (v.title.isEmpty()) View.GONE else View.VISIBLE
        title.setTextColor(when (v.tone) {
            SosDelivery.Tone.OK -> WatchColors.OK
            SosDelivery.Tone.WARN -> WatchColors.WARN
            SosDelivery.Tone.ERROR -> WatchColors.ERROR
            SosDelivery.Tone.PROGRESS -> WatchColors.LABEL
        })
        detail.text = v.detail
        detail.visibility = if (v.detail.isEmpty()) View.GONE else View.VISIBLE
        call100.visibility = if (v.offerCall) View.VISIBLE else View.GONE
        call101.visibility = if (v.offerCall) View.VISIBLE else View.GONE
        retry.visibility = if (v.canRetry) View.VISIBLE else View.GONE
    }

    private fun text(sp: Float, bold: Boolean = false) = TextView(ctx).apply {
        textSize = sp
        gravity = Gravity.CENTER
        setTextColor(WatchColors.LABEL)
        if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = ctx.dp(4f).toInt() }
    }

    private fun button(label: String, color: Int, onClick: () -> Unit) = pillButton(ctx, label, color, onClick)
}

fun pillButton(ctx: Context, label: String, color: Int, onClick: () -> Unit) = Button(ctx).apply {
    text = label
    isAllCaps = false
    textSize = 15f
    setTextColor(Color.WHITE)
    background = GradientDrawable().apply { setColor(color); cornerRadius = ctx.dp(24f) }
    minHeight = ctx.dp(48f).toInt()
    setOnClickListener { onClick() }
    layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        .apply { topMargin = ctx.dp(6f).toInt() }
}
