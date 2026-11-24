package org.lifeguard.app.ui

import android.animation.ValueAnimator
import android.app.Activity
import android.content.Context
import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import com.google.android.material.button.MaterialButton
import org.lifeguard.app.R
import org.lifeguard.app.api.ApiException
import java.io.IOException

/**
 * בניית מסכים פשוטים בקוד (Views קלאסיים, RTL לפי ה-locale), לפי מערכת העיצוב (design/tokens.json):
 * רקע מקובץ (lg_bg), כרטיסים לבנים/נייבי בפינות 16, כפתורי Material בפינות 12, צבעים מהטוקנים בבהיר ובכהה.
 */
object Ui {
    fun dp(a: Context, v: Int) = (v * a.resources.displayMetrics.density).toInt()

    fun color(a: Context, res: Int) = ContextCompat.getColor(a, res)

    /** ScrollView + LinearLayout אנכי; מחזיר את ה-LinearLayout */
    fun page(a: Activity): LinearLayout {
        val col = LinearLayout(a).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(a, 16), dp(a, 8), dp(a, 16), dp(a, 32))
        }
        a.setContentView(ScrollView(a).apply {
            setBackgroundColor(color(a, R.color.lg_bg))
            isFillViewport = true
            addView(col)
        })
        return col
    }

    fun text(a: Activity, s: CharSequence, sizeSp: Float = 15f, bold: Boolean = false, color: Int? = null,
             topDp: Int = 4): TextView = TextView(a).apply {
        text = s; textSize = sizeSp
        setLineSpacing(0f, 1.12f)
        if (bold) setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL), Typeface.NORMAL)
        setTextColor(color ?: this@Ui.color(a, R.color.lg_label))
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(a, topDp) }
    }

    /** כותרת קטע (Title 3) - מסומנת ככותרת לנגישות */
    fun header(a: Activity, s: CharSequence) = text(a, s, 20f, bold = true, topDp = 24).apply {
        ViewCompat.setAccessibilityHeading(this, true)
    }

    /** כפתור Material: מלא (פעולה ראשית) או מתאר (משנית). רוחב ברירת מחדל לפי ההורה. */
    fun button(a: Activity, s: CharSequence, outlined: Boolean = false, onClick: (View) -> Unit): Button =
        MaterialButton(a, null, if (outlined) com.google.android.material.R.attr.materialButtonOutlinedStyle
        else com.google.android.material.R.attr.materialButtonStyle).apply {
            text = s; setOnClickListener(onClick)
        }

    /** כפתור טונאלי (כחול בהיר, טקסט נייבי) - פעולה משנית בולטת */
    fun tonalButton(a: Activity, s: CharSequence, onClick: (View) -> Unit): Button =
        MaterialButton(a, null, R.attr.lgTonalButtonStyle).apply { text = s; setOnClickListener(onClick) }

    /** שורת רשימה (טקסט בהתחלה, חץ בסוף) - כמו בהגדרות */
    fun listRow(a: Activity, s: CharSequence, onClick: (View) -> Unit): MaterialButton =
        MaterialButton(a, null, R.attr.lgRowButtonStyle).apply {
            text = s; setOnClickListener(onClick)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

    /** כרטיס: R.color.card_alert -> כרטיס חירום (אדום בהיר עם מסגרת), אחרת כרטיס רגיל */
    fun card(a: Activity, colorRes: Int = R.color.card_neutral): LinearLayout = LinearLayout(a).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(a, 16), dp(a, 14), dp(a, 16), dp(a, 14))
        setCardTone(this, alert = colorRes == R.color.card_alert)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(a, 12) }
    }

    /** מחליף את רקע הכרטיס בלי לאבד את הפינות המעוגלות (במקום setBackgroundColor) */
    fun setCardTone(v: View, alert: Boolean) {
        v.setBackgroundResource(if (alert) R.drawable.bg_card_alert else R.drawable.bg_card)
        v.elevation = if (alert) 0f else v.resources.getDimension(R.dimen.lg_card_elevation)
    }

    fun row(a: Activity): LinearLayout = LinearLayout(a).apply { orientation = LinearLayout.HORIZONTAL }

    fun label(a: Activity) = color(a, R.color.lg_label)
    fun gray(a: Activity) = color(a, R.color.lg_label_secondary)
    fun red(a: Activity) = color(a, R.color.lg_sos)
    fun green(a: Activity) = color(a, R.color.lg_success)
    fun amber(a: Activity) = color(a, R.color.lg_warning)

    /** כותרת + שורת משנה קטנה ומשנית (לאריחים במסך הבית) */
    fun twoLine(a: Context, title: CharSequence, sub: CharSequence): CharSequence = SpannableStringBuilder(title).apply {
        append("\n")
        val start = length
        append(sub)
        setSpan(RelativeSizeSpan(0.8f), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        setSpan(ForegroundColorSpan(color(a, R.color.lg_label_secondary)), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    }

    // ---------------- תנועה (tokens.motion): 160-360ms, cubic-bezier(.2,.8,.2,1), רק כשהמערכת מאפשרת אנימציות ----------------
    private val EASE = PathInterpolator(0.2f, 0.8f, 0.2f, 1f)

    /** false כשהמשתמש/ת כיבה/תה אנימציות (Developer options / Accessibility "Remove animations") */
    fun motionEnabled(): Boolean = ValueAnimator.areAnimatorsEnabled()

    /**
     * הופעה עדינה של כרטיס (שקיפות + תזוזה של 8dp, 240ms). קוסמטי בלבד: התצוגה כבר VISIBLE ונגישה מהרגע הראשון,
     * לא מעכב שום פעולה. כשאנימציות כבויות - מופיע מיד.
     */
    fun reveal(v: View) {
        v.animate().cancel()
        if (!motionEnabled()) { v.alpha = 1f; v.translationY = 0f; return }
        v.alpha = 0.4f
        v.translationY = dp(v.context, 8).toFloat()
        v.animate().alpha(1f).translationY(0f).setDuration(240).setInterpolator(EASE).start()
    }

    /** הודעת שגיאה בעברית: שגיאת שרת -> ההודעה מהשרת; רשת -> "לא זמין - אין חיבור לשרת" */
    fun errorText(a: Activity, e: Throwable?): String = when (e) {
        is ApiException -> e.message ?: "שגיאת שרת (${e.status})"
        is IOException -> a.getString(R.string.unavailable_offline)
        is IllegalStateException -> "כתובת השרת לא תקינה"
        is IllegalArgumentException -> "קלט לא תקין"
        null -> ""
        else -> e.javaClass.simpleName
    }
}
