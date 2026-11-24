package org.lifeguard.app.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.res.AssetManager
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.core.firstaid.ContentSource
import org.lifeguard.app.core.firstaid.DialButton
import org.lifeguard.app.core.firstaid.FirstAidLibrary
import org.lifeguard.app.core.firstaid.FirstAidScreenModel
import org.lifeguard.app.core.firstaid.FirstAidScreenModel.Companion as M
import org.lifeguard.app.core.firstaid.FirstAidView
import org.lifeguard.app.core.firstaid.GuidePage
import org.lifeguard.app.core.firstaid.ResultItem
import org.lifeguard.app.core.firstaid.SearchResult
import org.lifeguard.app.service.LocationTracker
import org.lifeguard.app.service.toGeo
import java.util.concurrent.Executors

/**
 * "עזרה ראשונה" - אופליין לגמרי: התוכן נארז ב-assets בזמן הבנייה (מ-first_aid/ במאגר), החיפוש מקומי
 * (core/firstaid), אין שום פנייה לרשת, ולא צריך התחברות או שרת. שורת חיפוש אחת למעלה (תמיד גלויה), בלי קטלוג מצבים;
 * תוצאות רק אחרי הקלדה. חיוג = ACTION_DIAL בלבד (המשתמש/ת לוחץ/ת "חייג" בחייגן; לא מתקשרים אוטומטית).
 * קישורי מקורות נפתחים רק בלחיצה יזומה, עם הערה שדרוש אינטרנט.
 */
class FirstAidActivity : AppCompatActivity() {
    private var model: FirstAidScreenModel? = null
    private lateinit var search: EditText
    private lateinit var status: TextView
    private lateinit var body: LinearLayout
    private lateinit var scroll: ScrollView
    private lateinit var ageGroup: ChipGroup
    private lateinit var bottomDial: Button
    private val chipIds = HashMap<String?, Int>()
    private val handler = Handler(Looper.getMainLooper())
    private var suppressWatcher = false
    private var syncingChips = false
    private val debounced = Runnable { val q = search.text.toString(); act { it.onQuery(q) } }
    /** כל הפעולות על המודל רצות בתהליכון אחד ברקע (החיפוש לא תוקע את המסך), לפי הסדר; מוצג רק הפלט האחרון */
    private val worker = Executors.newSingleThreadExecutor().asCoroutineDispatcher()
    private var seq = 0
    private var restore: Bundle? = null

    // Colours from the shared tokens (design/tokens.json), light / dark. Every text/background pair here is >= 4.5:1.
    private val INK by lazy { color(R.color.lg_label) }
    private val DARK by lazy { color(R.color.lg_label_secondary) }
    private val BG by lazy { color(R.color.lg_bg) }
    private val CARD by lazy { color(R.color.lg_surface) }
    private val RED by lazy { color(R.color.lg_sos) }                   // red text (on surface / light-red card)
    private val RED_FILL by lazy { color(R.color.lg_fill_sos) }         // fixed red fill, white text
    private val ON_FILL by lazy { color(R.color.lg_on_fill) }
    private val DANGER_BG by lazy { color(R.color.lg_sos_tint) }
    private val AMBER_BG by lazy { color(R.color.lg_warning_tint) }
    private val AMBER_TEXT by lazy { color(R.color.lg_warning) }
    private val NAVY by lazy { color(R.color.lg_fill_navy) }            // safety procedure: fixed navy, white text
    private val BLUE by lazy { color(R.color.lg_accent) }
    private val GREEN by lazy { color(R.color.lg_success) }
    private val urgencyColors by lazy {
        mapOf("critical" to RED_FILL, "high" to color(R.color.lg_fill_warning),
            "medium" to color(R.color.lg_fill_accent), "low" to color(R.color.lg_fill_neutral))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.first_aid_title)
        restore = savedInstanceState
        buildLayout()
        savedInstanceState?.getString(KEY_QUERY)?.let { setQueryText(it) }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                val m = model
                if (m == null) { finish(); return }
                val my = ++seq
                lifecycleScope.launch {
                    val v = withContext(worker) { m.back()?.let { it to m.age } }
                    if (v != null) { if (my == seq) render(v.first, v.second) } else finish()
                }
            }
        })
        val cached = cachedLibrary
        if (cached != null) onLibrary(cached)
        else {
            renderLoading()
            lifecycleScope.launch {
                val lib = withContext(Dispatchers.IO) { loadLibrary(assets) }
                onLibrary(lib)
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        model?.let { m ->
            outState.putString(KEY_QUERY, search.text.toString())
            outState.putString(KEY_AGE, m.age)
            outState.putString(KEY_CURRENT, m.current)
        }
    }

    override fun onDestroy() {
        handler.removeCallbacks(debounced)
        super.onDestroy()
        worker.close()
    }

    private fun onLibrary(lib: FirstAidLibrary) {
        val m = FirstAidScreenModel(lib)
        model = m
        bottomDial.text = "📞 מד״א ${lib.mda}"
        bottomDial.contentDescription = "חיוג למד״א ${lib.mda}. נפתח בחייגן, לא מתקשר אוטומטית"
        bottomDial.setOnClickListener { dial(lib.mda) }
        val r = restore
        restore = null
        val q = search.text.toString()
        if (r != null) {
            val age = r.getString(KEY_AGE)
            val cur = r.getString(KEY_CURRENT)
            act { mm -> mm.setAge(age); val v = mm.onQuery(q); cur?.let { mm.openGuide(it) } ?: v }
        } else act { it.onQuery(q) }
    }

    // ------------------------------------------------------------ מבנה קבוע: חיפוש למעלה, גוף נגלל, חיוג למטה
    private fun buildLayout() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(BG) }
        val top = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(8), dp(12), dp(4))
        }
        top.addView(text(M.OFFLINE_NOTE, 13f, color = GREEN))
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundResource(R.drawable.bg_search_field)
            setPadding(dp(12), dp(2), dp(4), dp(2))
        }
        search = EditText(this).apply {
            hint = M.PLACEHOLDER
            textSize = 20f
            setTextColor(INK)
            setHintTextColor(color(R.color.lg_label_tertiary))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            imeOptions = EditorInfo.IME_ACTION_SEARCH
            background = null
            minHeight = dp(56)
            textDirection = View.TEXT_DIRECTION_ANY_RTL
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
            contentDescription = "חיפוש מצב חירום. " + M.PLACEHOLDER
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun afterTextChanged(s: Editable?) {
                    if (suppressWatcher) return
                    handler.removeCallbacks(debounced)
                    handler.postDelayed(debounced, M.SEARCH_DEBOUNCE_MS)
                }
            })
            setOnEditorActionListener { _, actionId, _ ->
                if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                    handler.removeCallbacks(debounced); debounced.run(); hideKeyboard(); true
                } else false
            }
        }
        bar.addView(search)
        bar.addView(com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
            text = "✕"; textSize = 20f
            contentDescription = "ניקוי שורת החיפוש"
            setOnClickListener {
                setQueryText("")
                act { it.clear() }
                search.requestFocus()
            }
        })
        top.addView(bar)

        // גיל הנפגע/ת: לא ידוע / מבוגר / ילד מעל גיל שנה / תינוק מתחת לגיל שנה
        val ageRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        ageRow.addView(text("גיל הנפגע/ת:", 14f, bold = true, topDp = 0).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        })
        ageGroup = ChipGroup(this).apply { isSingleLine = true; isSingleSelection = true; isSelectionRequired = true }
        for ((key, label) in M.AGE_CHIPS) {
            val chip = Chip(this).apply {
                id = View.generateViewId()
                text = label
                textSize = 15f
                isCheckable = true
                contentDescription = "גיל הנפגע/ת: $label"
            }
            chipIds[key] = chip.id
            ageGroup.addView(chip)
        }
        ageGroup.check(chipIds.getValue(null))
        ageGroup.setOnCheckedStateChangeListener { _, ids ->
            if (syncingChips) return@setOnCheckedStateChangeListener
            val key = chipIds.entries.firstOrNull { it.value == ids.firstOrNull() }?.key
            act { it.setAge(key) }
        }
        ageRow.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(ageGroup) })
        top.addView(ageRow)
        status = text("", 15f, bold = true, color = INK, topDp = 2)
        top.addView(status)
        root.addView(top, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), 0, dp(12), dp(16)) }
        scroll = ScrollView(this).apply { addView(body) }
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        // חיוג 101 תמיד גלוי בתחתית
        // red that stays the same in light and dark (lg_fill_sos), white text
        bottomDial = MaterialButton(this, null, R.attr.lgSosButtonStyle).apply {
            text = "📞 מד״א 101"; textSize = 20f
            setTypeface(typeface, Typeface.BOLD)
            minHeight = dp(64)
            contentDescription = "חיוג למד״א 101. נפתח בחייגן, לא מתקשר אוטומטית"
            setOnClickListener { dial("101") }
        }
        root.addView(bottomDial, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { setMargins(dp(12), dp(4), dp(12), dp(8)) })
        setContentView(root)
        search.requestFocus()
    }

    private fun setQueryText(s: String) {
        suppressWatcher = true
        search.setText(s)
        search.setSelection(s.length)
        suppressWatcher = false
        handler.removeCallbacks(debounced)
    }

    private fun act(op: (FirstAidScreenModel) -> FirstAidView?) {
        val m = model ?: return
        val my = ++seq
        lifecycleScope.launch {
            val v = withContext(worker) { op(m)?.let { it to m.age } } ?: return@launch
            if (my == seq) render(v.first, v.second)      // פעולה חדשה יותר כבר בדרך - היא תוצג
        }
    }

    private fun syncAgeChips(age: String?) {
        val want = chipIds[age] ?: chipIds.getValue(null)
        if (ageGroup.checkedChipId != want) {
            syncingChips = true
            ageGroup.check(want)
            syncingChips = false
        }
    }

    // ------------------------------------------------------------ תצוגות
    private fun clearBody() {
        body.removeAllViews()
        scroll.scrollTo(0, 0)
    }

    private fun renderLoading() {
        clearBody()
        status.text = ""
        body.addView(text("טוען את המדריכים מהמכשיר…", 16f))
        emergencyBox(body, safety = false)
    }

    private fun render(v: FirstAidView, age: String?) {
        syncAgeChips(age)
        clearBody()
        when (v) {
            is FirstAidView.Landing -> renderLanding(v)
            is FirstAidView.Results -> renderResults(v)
            is FirstAidView.NoMatch -> renderNoMatch(v)
            is FirstAidView.AgePrompt -> renderAgePrompt(v)
            is FirstAidView.GuideView -> renderGuide(v.page)
        }
    }

    private fun renderLanding(v: FirstAidView.Landing) {
        status.text = ""
        val c = card(CARD)
        v.loadProblem?.let { c.addView(text(it, 18f, bold = true, color = RED)) }
        c.addView(text(v.hint, 18f, color = INK))
        c.addView(text(v.hint2, 15f, color = DARK))
        body.addView(c)
        emergencyBox(body, safety = false)
        disclaimer()
    }

    private fun renderNoMatch(v: FirstAidView.NoMatch) {
        status.text = ""
        val c = card(CARD)
        c.addView(text(v.text, 18f, bold = true, color = RED))
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        row.addView(text("אולי:", 14f, color = DARK, topDp = 0).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        })
        for (s in v.suggestions) row.addView(Chip(this).apply {
            text = s; textSize = 14f
            contentDescription = "חיפוש: $s"
            setOnClickListener { setQueryText(s); act { it.suggestion(s) } }
        })
        c.addView(HorizontalScrollView(this).apply { addView(row) })
        body.addView(c)
        emergencyBox(body, safety = false)
    }

    private fun renderResults(v: FirstAidView.Results) {
        status.text = v.status
        status.setTextColor(if (v.ambiguous) AMBER_TEXT else DARK)
        for (item in v.items) when (item) {
            is ResultItem.Card -> resultCard(item.result)
            is ResultItem.FamilyChooser -> familyCard(item)
        }
        disclaimer()
    }

    private fun resultCard(r: SearchResult) {
        val c = card(CARD)
        val open = View.OnClickListener { openGuide(r.id) }
        c.isClickable = true; c.isFocusable = true
        c.setOnClickListener(open)
        c.contentDescription = "${r.title}. ${r.urgencyHe}. ${r.summary}" + (r.ageNote?.let { ". $it" } ?: "") + ". הקישו לפתיחת המדריך"
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(text(r.title, 19f, bold = true, color = INK, topDp = 0).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        head.addView(urgencyChip(r.urgency, r.urgencyHe))
        c.addView(head)
        c.addView(text(r.summary, 15f, color = DARK).apply { maxLines = 2; ellipsize = android.text.TextUtils.TruncateAt.END })
        r.ageNote?.let { c.addView(text("ℹ️ $it", 14f, bold = true, color = AMBER_TEXT)) }
        c.addView(MaterialButton(this).apply {
            text = "פתח מדריך"; textSize = 15f
            contentDescription = "פתח מדריך: ${r.title}"
            setOnClickListener(open)
        })
        body.addView(c)
    }

    private fun familyCard(f: ResultItem.FamilyChooser) {
        val c = card(CARD)
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(text(f.title, 19f, bold = true, color = INK, topDp = 0).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            ViewCompat.setAccessibilityHeading(this, true)
        })
        head.addView(urgencyChip("critical", FirstAidLibrary.URGENCY_HE.getValue("critical")))
        c.addView(head)
        c.addView(text(f.question, 17f, bold = true, color = RED))
        for ((key, label) in f.choices) c.addView(MaterialButton(this, null, R.attr.lgTonalButtonStyle).apply {
            text = label; textSize = 17f
            contentDescription = "${f.title}: $label"
            setOnClickListener { hideKeyboard(); act { it.chooseAge(key, f.anyId) } }
        })
        body.addView(c)
    }

    private fun renderAgePrompt(v: FirstAidView.AgePrompt) {
        status.text = ""
        val c = card(CARD)
        c.addView(text(v.title, 20f, bold = true, color = INK).apply { ViewCompat.setAccessibilityHeading(this, true) })
        c.addView(text(v.question, 18f, bold = true, color = RED))
        for ((key, label) in v.choices) c.addView(MaterialButton(this, null, R.attr.lgTonalButtonStyle).apply {
            text = label; textSize = 18f
            contentDescription = "גיל הנפגע/ת: $label"
            setOnClickListener { act { it.chooseAge(key, v.guideId) } }
        })
        body.addView(c)
        emergencyBox(body, safety = false)
    }

    private fun openGuide(id: String) {
        hideKeyboard()
        act { it.openGuide(id) }
    }

    private fun renderGuide(p: GuidePage) {
        status.text = ""
        val g = p.guide
        if (p.canGoBack) body.addView(com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
            text = "→ חזרה לתוצאות"; textSize = 15f
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            setOnClickListener { act { it.back() } }
        })
        // 1. כותרת + דחיפות
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(text(g.title, 23f, bold = true, color = INK, topDp = 0).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            ViewCompat.setAccessibilityHeading(this, true)
        })
        head.addView(urgencyChip(g.urgency, p.urgencyHe))
        body.addView(head)
        // 2. אזהרת חירום בולטת
        val warn = card(if (p.critical) RED_FILL else AMBER_BG)
        warn.addView(text("🚨 " + p.emergencyBanner, if (p.critical) 19f else 17f, bold = true,
            color = if (p.critical) ON_FILL else INK, topDp = 0))
        body.addView(warn)
        // נוהל בטיחות - מופרד במפורש מעזרה ראשונה רפואית
        p.safetyLabel?.let { body.addView(card(NAVY).apply { addView(text("🛡️ $it", 16f, bold = true, color = ON_FILL, topDp = 0)) }) }
        // תוכן שמשתנה ומחייב מקור רשמי עדכני (התרעות טילים)
        p.officialNote?.let { body.addView(card(AMBER_BG).apply { addView(text("📢 $it", 16f, bold = true, color = INK, topDp = 0)) }) }
        // גיל
        body.addView(card(CARD).apply {
            addView(text("👤 " + p.ageLine, 15f, color = INK, topDp = 0))
            p.ageNote?.let { addView(text("⚠️ $it", 16f, bold = true, color = RED)) }
        })
        // 3-5: צעדים, אסור, מתי להזעיק, סימנים, חשוב לדעת
        for (s in p.sections) {
            val c = card(if (s.danger) DANGER_BG else CARD, stroke = true)
            val icon = when (s.key) {
                "what_to_do" -> "✅ "; "do_not" -> "⛔ "; "red_flags" -> "🚑 "; "recognition" -> "🔎 "; else -> "📝 "
            }
            c.addView(text(icon + s.title, 19f, bold = true, color = if (s.danger) RED else INK, topDp = 0).apply {
                ViewCompat.setAccessibilityHeading(this, true)
            })
            s.items.forEachIndexed { i, item ->
                c.addView(text((if (s.numbered) "${i + 1}. " else "• ") + item, if (s.numbered) 18f else 16f, color = INK, topDp = 6))
            }
            body.addView(c)
        }
        p.settingLine?.let { body.addView(text("📍 $it", 14f, color = DARK)) }
        p.related?.let { (rid, rtitle) ->
            body.addView(com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
                text = "מדריך קשור: $rtitle"; textSize = 15f
                contentDescription = "פתיחת מדריך קשור: $rtitle"
                setOnClickListener { openGuide(rid) }
            })
        }
        p.poisonCenter?.let { body.addView(card(CARD).apply { addView(text("☠️ $it", 16f, bold = true, color = INK, topDp = 0)) }) }
        emergencyBox(body, safety = g.isSafetyProcedure, buttons = p.dial)
        // מקור, סטטוס בדיקה רפואית, גרסה - משני וקומפקטי (אבל תמיד גלוי)
        val meta = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, dp(10), 0, 0) }
        meta.addView(text(p.reviewStatus, 14f, bold = true, color = AMBER_TEXT))
        meta.addView(text(p.metaLine, 13f, color = DARK))
        for (s in p.sources) {
            meta.addView(text("מקור: ${s.title}", 13f, color = DARK, topDp = 2))
            val url = s.url
            if (url != null && (url.startsWith("https://") || url.startsWith("http://"))) {
                meta.addView(text(M.LINK_NOTE, 13f, color = BLUE, topDp = 0).apply {
                    isClickable = true; isFocusable = true
                    contentDescription = "פתיחת המקור ${s.title} בדפדפן - דורש אינטרנט"
                    setOnClickListener { runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } }
                })
            }
        }
        body.addView(meta)
        disclaimer()
    }

    // ------------------------------------------------------------ חיוג + מיקום אחרון
    private fun emergencyBox(parent: LinearLayout, safety: Boolean, buttons: List<DialButton>? = null) {
        val c = card(CARD)
        c.addView(text("בסכנת חיים:", 17f, bold = true, color = INK, topDp = 0))
        val list = buttons ?: model?.dialButtons(safety) ?: listOf(DialButton("mda", "מד״א 101", "101"))
        // MDA: fixed red with white text; other numbers: tonal button
        for (b in list) c.addView(MaterialButton(this, null, if (b.key == "mda") R.attr.lgSosButtonStyle else R.attr.lgTonalButtonStyle).apply {
            text = "📞 " + b.label; textSize = 18f
            contentDescription = "חיוג ל${b.label}. נפתח בחייגן, לא מתקשר אוטומטית"
            setOnClickListener { dial(b.number) }
        })
        dialNote = text(M.DIAL_NOTE, 13f, color = DARK)
        c.addView(dialNote)
        lastLocationText()?.let { c.addView(text("📍 $it", 14f, color = INK)) }
        parent.addView(c)
    }

    private var dialNote: TextView? = null

    /** ACTION_DIAL בלבד - פותח את החייגן עם המספר; המשתמש/ת מחליט/ה. לא מעמידים פנים שיש קליטה. */
    private fun dial(number: String) {
        val digits = number.filter { it.isDigit() }
        try {
            startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$digits")))
        } catch (e: ActivityNotFoundException) {
            dialNote?.apply { text = "אין במכשיר הזה אפליקציית חיוג. חייגו $number מטלפון אחר או בקשו ממישהו להזעיק עזרה."; setTextColor(RED) }
        }
    }

    /** המיקום האחרון שכבר שמור במכשיר (LocationManager.getLastKnownLocation - בלי בקשה חדשה ובלי רשת) */
    private fun lastLocationText(): String? {
        val fix = runCatching { LG.lastFix ?: LocationTracker(this).lastKnown()?.toGeo() }.getOrNull() ?: return null
        return M.formatLastLocation(fix.lat, fix.lon, fix.isPrecise, fix.accuracyM, fix.capturedAt,
            System.currentTimeMillis() / 1000.0)
    }

    private fun disclaimer() {
        val lib = model?.lib ?: return
        body.addView(text("⚕️ " + lib.disclaimer, 13f, color = DARK, topDp = 12))
    }

    // ------------------------------------------------------------ עזרים
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun color(res: Int) = ContextCompat.getColor(this, res)

    private fun text(s: CharSequence, sizeSp: Float, bold: Boolean = false, color: Int = INK, topDp: Int = 4) =
        TextView(this).apply {
            text = s; textSize = sizeSp; setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
            textDirection = View.TEXT_DIRECTION_ANY_RTL
            setLineSpacing(0f, 1.15f)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(topDp) }
        }

    private fun card(bg: Int, stroke: Boolean = false) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(14), dp(16), dp(14))
        background = GradientDrawable().apply {
            setColor(bg); cornerRadius = resources.getDimension(R.dimen.lg_radius_card)
            if (stroke) setStroke(dp(1), color(R.color.lg_separator))
        }
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(10) }
    }

    private fun urgencyChip(urgency: String, label: String) = TextView(this).apply {
        text = " $label "
        textSize = 14f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(ON_FILL)
        setPadding(dp(8), dp(2), dp(8), dp(2))
        background = GradientDrawable().apply { setColor(urgencyColors[urgency] ?: color(R.color.lg_fill_neutral)); cornerRadius = dp(12).toFloat() }
        contentDescription = "דחיפות: $label"
    }

    private fun hideKeyboard() {
        getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(search.windowToken, 0)
    }

    /** קורא את התוכן מ-assets/first_aid (נארז בזמן הבנייה) */
    class AssetContentSource(private val assets: AssetManager) : ContentSource {
        override fun read(path: String): String = ContentSource.readStream(assets.open("first_aid/$path"))
    }

    companion object {
        private const val KEY_QUERY = "query"
        private const val KEY_AGE = "age"
        private const val KEY_CURRENT = "current"

        @Volatile private var cachedLibrary: FirstAidLibrary? = null

        /** נטען פעם אחת לתהליך (רק מהמכשיר - assets) */
        fun loadLibrary(assets: AssetManager): FirstAidLibrary =
            cachedLibrary ?: FirstAidLibrary.safeLoad(AssetContentSource(assets)).also { cachedLibrary = it }

        fun intent(ctx: Context) = Intent(ctx, FirstAidActivity::class.java)
    }
}
