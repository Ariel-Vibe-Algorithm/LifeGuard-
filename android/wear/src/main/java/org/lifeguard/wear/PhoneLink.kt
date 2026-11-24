package org.lifeguard.wear

import android.content.Context
import android.util.Log
import com.google.android.gms.wearable.CapabilityClient
import com.google.android.gms.wearable.Wearable
import org.lifeguard.app.wearlink.WearProtocol
import org.lifeguard.wear.logic.SosDelivery

/**
 * שעון -> טלפון (MessageClient). מחפש צומת עם היכולת lifeguard_phone (= LifeGuard מותקנת בטלפון), מעדיף חיבור ישיר
 * (Bluetooth, isNearby) על פני ענן. הצלחת sendMessage = ההודעה נמסרה לשכבת הקישור בלבד - האישור האמיתי הוא SosAck.
 * callback תמיד ב-main thread (ברירת המחדל של Tasks).
 */
object PhoneLink {
    private const val TAG = "LifeGuardWatch"

    sealed class Outcome {
        data class Sent(val nodeId: String) : Outcome()
        data class NoPhone(val reason: SosDelivery.NoPhoneReason) : Outcome()
        object Failed : Outcome()
    }

    fun send(ctx: Context, req: WearProtocol.SosRequest, cb: (Outcome) -> Unit) {
        val bytes = WearProtocol.encodeRequest(req)
        try {
            Wearable.getCapabilityClient(ctx)
                .getCapability(WearProtocol.CAPABILITY_PHONE, CapabilityClient.FILTER_REACHABLE)
                .addOnSuccessListener { info ->
                    val node = info.nodes.sortedByDescending { it.isNearby }.firstOrNull()
                    if (node == null) noPhone(ctx, cb)
                    else Wearable.getMessageClient(ctx).sendMessage(node.id, WearProtocol.PATH_SOS, bytes)
                        .addOnSuccessListener { cb(Outcome.Sent(node.id)) }
                        .addOnFailureListener { e -> Log.w(TAG, "sendMessage failed: ${e.javaClass.simpleName}"); cb(Outcome.Failed) }
                }
                .addOnFailureListener { e -> Log.w(TAG, "capability lookup failed: ${e.javaClass.simpleName}"); noPhone(ctx, cb) }
        } catch (e: Exception) {
            Log.w(TAG, "Data Layer unavailable: ${e.javaClass.simpleName}")
            cb(Outcome.Failed)
        }
    }

    /** אין צומת עם LifeGuard: האם יש בכלל טלפון מחובר (ואז האפליקציה חסרה בו) או שאין חיבור */
    private fun noPhone(ctx: Context, cb: (Outcome) -> Unit) {
        Wearable.getNodeClient(ctx).connectedNodes
            .addOnSuccessListener { nodes ->
                cb(Outcome.NoPhone(if (nodes.isEmpty()) SosDelivery.NoPhoneReason.NOT_CONNECTED else SosDelivery.NoPhoneReason.APP_MISSING))
            }
            .addOnFailureListener { cb(Outcome.NoPhone(SosDelivery.NoPhoneReason.NOT_CONNECTED)) }
    }
}
