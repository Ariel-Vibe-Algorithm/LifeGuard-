package org.lifeguard.app.ui

import android.app.Activity
import android.app.KeyguardManager
import android.content.Intent
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * אישור בנעילת המסך של המכשיר (PIN / תבנית / סיסמה) לפני צפייה/ייצוא של ראיות.
 * API 30+: BiometricPrompt של המערכת עם DEVICE_CREDENTIAL בלבד. API 26-29: KeyguardManager.createConfirmDeviceCredentialIntent.
 * בלי ספריות נוספות. מכשיר בלי נעילת מסך -> לא זמין (מוסבר למשתמש/ת).
 * חייב להיווצר ב-onCreate של ה-Activity (רישום ActivityResult).
 */
class CredentialGate(private val activity: AppCompatActivity) {
    private var pending: (() -> Unit)? = null
    private var onDenied: ((String) -> Unit)? = null

    private val legacy: ActivityResultLauncher<Intent> =
        activity.registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
            val ok = pending; pending = null
            if (r.resultCode == Activity.RESULT_OK) ok?.invoke() else onDenied?.invoke("האימות בוטל")
        }

    fun available(): Boolean = activity.getSystemService(KeyguardManager::class.java)?.isDeviceSecure == true

    fun confirm(title: String, onOk: () -> Unit, onFail: (String) -> Unit) {
        if (!available()) { onFail("כדי לצפות/לייצא תיעוד יש להגדיר נעילת מסך (PIN / תבנית / סיסמה) במכשיר"); return }
        onDenied = onFail
        if (Build.VERSION.SDK_INT >= 30) {
            val prompt = BiometricPrompt.Builder(activity)
                .setTitle(title)
                .setAllowedAuthenticators(BiometricManager.Authenticators.DEVICE_CREDENTIAL)
                .build()
            prompt.authenticate(CancellationSignal(), activity.mainExecutor, object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult?) = onOk()
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence?) =
                    onFail(errString?.toString() ?: "האימות נכשל")
            })
        } else {
            @Suppress("DEPRECATION")
            val i = activity.getSystemService(KeyguardManager::class.java).createConfirmDeviceCredentialIntent(title, null)
            if (i == null) { onFail("לא ניתן לבקש אימות במכשיר זה"); return }
            pending = onOk
            legacy.launch(i)
        }
    }
}
