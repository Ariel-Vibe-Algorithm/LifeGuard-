package org.lifeguard.app.store

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/** מפתח AES-256-GCM ב-Android Keystore (לא יוצא מהחומרה המאובטחת), נוצר בשימוש הראשון. */
object KeystoreAes {
    private const val KEYSTORE = "AndroidKeyStore"

    @Synchronized
    fun get(alias: String): SecretKey {
        val ks = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        gen.init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build())
        return gen.generateKey()
    }
}

/**
 * מפתח נפרד ליומן הראיות. בלי דרישת אימות משתמש לשימוש - התיעוד חייב לעבוד גם כשהטלפון נעול בזמן SOS. הצפייה/הייצוא
 * מוגנים בנעילת המסך ברמת הממשק (CredentialGate).
 */
object EvidenceKey {
    fun get(): SecretKey = KeystoreAes.get("lifeguard_evidence_v1")
}

/** מפתח לתורים הלא-מקוונים (אירועי ביקורת, העלאת הקלטות). */
object QueueKey {
    fun get(): SecretKey = KeystoreAes.get("lifeguard_queues_v1")
}
