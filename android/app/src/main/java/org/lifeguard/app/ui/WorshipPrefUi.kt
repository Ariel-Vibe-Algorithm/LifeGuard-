package org.lifeguard.app.ui

import android.content.Context
import android.view.View
import android.view.ViewGroup
import android.widget.RadioGroup
import com.google.android.material.radiobutton.MaterialRadioButton
import org.lifeguard.app.core.WorshipPref

/**
 * בחירת מקום התפילה למפלט אחרון (WorshipPref) - בהרשמה ובהגדרות. ההעדפה נשמרת רק במכשיר (SecureStore, מוצפן).
 */
object WorshipPrefUi {
    /** קבוצת כפתורי רדיו עם חמש האפשרויות; current מסומן. onPick נקרא רק בבחירה של המשתמש/ת. */
    fun radioGroup(ctx: Context, current: String, onPick: (String) -> Unit): RadioGroup {
        val group = RadioGroup(ctx).apply {
            orientation = RadioGroup.VERTICAL
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        val ids = HashMap<Int, String>()
        for ((key, label) in WorshipPref.OPTIONS) {
            val rb = MaterialRadioButton(ctx).apply {
                id = View.generateViewId()
                text = label
                textSize = 15f
                tag = key
            }
            group.addView(rb, RadioGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            ids[rb.id] = key
            if (key == WorshipPref.normalize(current)) rb.isChecked = true
        }
        group.setOnCheckedChangeListener { _, checkedId -> ids[checkedId]?.let(onPick) }
        return group
    }
}
