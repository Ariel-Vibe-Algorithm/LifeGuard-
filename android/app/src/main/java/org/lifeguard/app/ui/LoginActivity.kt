package org.lifeguard.app.ui

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.R
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.core.UrlPolicy
import org.lifeguard.app.core.WorshipPref
import org.lifeguard.app.databinding.ActivityLoginBinding
import org.lifeguard.app.fcm.FcmManager
import org.lifeguard.app.service.LifeGuardService
import java.io.IOException

class LoginActivity : AppCompatActivity() {
    private lateinit var b: ActivityLoginBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(b.root)
        title = getString(R.string.login_title)
        b.serverUrl.setText(LG.store.serverUrl)
        b.loginBtn.setOnClickListener { submit(register = false) }
        b.registerBtn.setOnClickListener { submit(register = true) }
        b.firstAidBtn.setOnClickListener { startActivity(FirstAidActivity.intent(this)) }
        // רשות: מקום תפילה כמפלט אחרון. נשמר רק במכשיר (מוצפן) ורק אם נבחר - לא נשלח לשרת עם ההרשמה
        b.worshipExplain.text = WorshipPref.EXPLAIN_HE
        b.worshipBox.addView(WorshipPrefUi.radioGroup(this, LG.store.worshipFallback) { key -> worshipPick = key })
    }

    /** הבחירה במסך הזה (null = לא נגעו) - נשמרת מקומית אחרי התחברות/הרשמה מוצלחת */
    private var worshipPick: String? = null

    private fun submit(register: Boolean) {
        val url = when (val r = UrlPolicy.normalize(b.serverUrl.text.toString())) {
            is UrlPolicy.Result.Ok -> r.baseUrl
            is UrlPolicy.Result.Error -> { b.error.text = r.messageHe; return }
        }
        val user = b.username.text.toString().trim()
        val pw = b.password.text.toString()
        val display = b.displayName.text.toString().trim().ifEmpty { user }
        if (user.isEmpty() || pw.isEmpty()) { b.error.text = getString(R.string.username) + " / " + getString(R.string.password); return }
        busy(true)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val api = ApiClient(url, { null })
                    if (register) api.register(user, pw, display)
                    api.login(user, pw, "Android ${Build.VERSION.RELEASE}")
                }
            }
            busy(false)
            result.onSuccess { r ->
                LG.store.serverUrl = url
                LG.store.token = r.token
                LG.store.username = r.username
                LG.store.displayName = r.displayName
                LG.store.lastNotificationId = -1     // מתחילים מה-cursor של השרת
                worshipPick?.let { LG.store.worshipFallback = it }   // מקומי בלבד, מוצפן
                LifeGuardService.start(this@LoginActivity)
                FcmManager.onLogin()
                startActivity(Intent(this@LoginActivity, MainActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
                finish()
            }.onFailure { e ->
                b.error.text = when (e) {
                    is ApiException -> e.message
                    is IOException -> getString(R.string.network_error)
                    else -> e.message ?: e.javaClass.simpleName
                }
            }
        }
    }

    private fun busy(on: Boolean) {
        b.progress.visibility = if (on) View.VISIBLE else View.GONE
        b.loginBtn.isEnabled = !on; b.registerBtn.isEnabled = !on
        if (on) b.error.text = ""
    }
}
