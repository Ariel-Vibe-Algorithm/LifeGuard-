package org.lifeguard.app.wear

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.CapabilityClient
import com.google.android.gms.wearable.Wearable
import org.lifeguard.app.core.PendingSos
import org.lifeguard.app.wearlink.WearProtocol

/**
 * טלפון -> שעון: אישור קבלה (SosAck) ומצב המסירה האמיתי (SosStatus) של SOS שנפתח מהשעון.
 * רק עובדות שהטלפון יודע: "נשמר ב-outbox", "השרת פתח אירוע" (+ activation שהשרת החליט), "מנסה שוב", SMS הועבר
 * לשליחה, 422 low_confidence. אין כאן שום "נמסר לאנשי הקשר" - הטלפון לא יודע את זה ברגע השליחה.
 * כל כישלון (אין Google Play Services / אין שעון) נבלע: השעון ממילא מציג "אין אישור" אם לא קיבל כלום.
 */
object WearRelay {
    private const val TAG = "LifeGuardWear"

    fun sendAck(ctx: Context, nodeId: String, ack: WearProtocol.SosAck) {
        runCatching {
            Wearable.getMessageClient(ctx).sendMessage(nodeId, WearProtocol.PATH_SOS_ACK, WearProtocol.encodeAck(ack))
                .addOnFailureListener { Log.w(TAG, "ack not sent: ${it.javaClass.simpleName}") }
        }.onFailure { Log.w(TAG, "ack not sent: ${it.javaClass.simpleName}") }
    }

    /** נקרא מ-IncidentManager. לא עושה כלום אם ה-SOS לא נפתח מהשעון. */
    fun status(ctx: Context, p: PendingSos, state: WearProtocol.PhoneState, activation: String? = null,
               attempts: Int = p.attempts, smsCount: Int = 0) {
        val (_, requestId) = WearProtocol.parsePhoneSource(p.source) ?: return
        val bytes = WearProtocol.encodeStatus(WearProtocol.SosStatus(requestId, p.clientId, state, activation, attempts, smsCount))
        runCatching {
            Wearable.getCapabilityClient(ctx)
                .getCapability(WearProtocol.CAPABILITY_WATCH, CapabilityClient.FILTER_REACHABLE)
                .addOnSuccessListener { info ->
                    for (n in info.nodes) {
                        Wearable.getMessageClient(ctx).sendMessage(n.id, WearProtocol.PATH_SOS_STATUS, bytes)
                            .addOnFailureListener { Log.w(TAG, "status not sent: ${it.javaClass.simpleName}") }
                    }
                }
                .addOnFailureListener { Log.w(TAG, "no watch capability: ${it.javaClass.simpleName}") }
        }.onFailure { Log.w(TAG, "status not sent: ${it.javaClass.simpleName}") }
    }
}
