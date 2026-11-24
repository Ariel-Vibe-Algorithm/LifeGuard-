package org.lifeguard.app

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import org.lifeguard.app.alert.AlarmPlayer
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiClient
import org.lifeguard.app.api.GeoPoint
import org.lifeguard.app.core.ActivationPolicy
import org.lifeguard.app.core.ConnectivityTracker
import org.lifeguard.app.core.Effect
import org.lifeguard.app.core.MotionFusion
import org.lifeguard.app.core.RiskAssessment
import org.lifeguard.app.core.RingStateMachine
import org.lifeguard.app.core.UrlPolicy
import org.lifeguard.app.core.WellnessMonitor
import org.lifeguard.app.service.Evidence
import org.lifeguard.app.service.IncidentManager
import org.lifeguard.app.store.SecureStore

/** אירועים לממשק (רענון מסכים פתוחים). */
sealed class UiEvent {
    data class IncidentUpdated(val incidentId: String, val event: String) : UiEvent()
    data class RingChanged(val incidentId: String, val ringing: Boolean) : UiEvent()
    data class IncidentClosed(val incidentId: String) : UiEvent()
    object StateChanged : UiEvent()
    object WellnessChanged : UiEvent()
    object LoggedOut : UiEvent()
}

/** מצב משותף לאפליקציה (שירות + מסכים) - תהליך אחד. */
object LG {
    lateinit var app: Application
        private set
    lateinit var store: SecureStore
        private set
    lateinit var alarm: AlarmPlayer
        private set
    lateinit var wellness: WellnessMonitor
        private set

    val ring = RingStateMachine()
    val connectivity = ConnectivityTracker()
    /** מיזוג חיישני תנועה (נפילה / מכה / מאבק / עצירה פתאומית) */
    val motion = MotionFusion()
    /** ההערכה שגרמה לשאלת "הכל בסדר?" האחרונה (נשלחת כ-risk אם השאלה מסלימה ל-SOS) */
    @Volatile var pendingMotion: RiskAssessment? = null
    /** "הכל בסדר?" של נסיעה בטוחה (escort_check מהשרת) שממתין לתשובה */
    @Volatile var escortCheck: Effect.EscortCheck? = null

    private val _events = MutableSharedFlow<UiEvent>(extraBufferCapacity = 64)
    val events: SharedFlow<UiEvent> = _events
    fun emit(e: UiEvent) { _events.tryEmit(e) }

    @Volatile var lastFix: GeoPoint? = null
    /** הצ'אט שפתוח כרגע על המסך (לא מציגים עליו התראה) */
    @Volatile var visibleChatIncident: String? = null

    private var apiCache: Pair<String, ApiClient>? = null

    fun init(application: Application) {
        app = application
        store = SecureStore(application)
        alarm = AlarmPlayer(application)
        wellness = WellnessMonitor(
            clock = { System.currentTimeMillis() / 1000.0 },
            // לא שואלים "הכל בסדר?" כשכבר יש אירוע פעיל/ממתין (כולל מצוקה שקטה - שם אסור להציג כלום)
            isBusy = { store.activeIncidentId != null || store.pendingSos != null },
            listener = object : WellnessMonitor.Listener {
                override fun onAsk(reason: String, reasonText: String, deadline: Double) {
                    alarm.startBeep()
                    Notifier.showWellness(app, reasonText)
                    emit(UiEvent.WellnessChanged)
                }

                override fun onEscalate(reason: String, why: String) {
                    alarm.stop()
                    Notifier.cancel(app, Notifier.ID_WELLNESS)
                    // trigger אוטומטי + ביטחון: השרת מחליט (נמוך -> 422, בינוני -> unconfirmed, גבוה -> מלא)
                    IncidentManager.startSos(app, "loud", source = "wellness_check:$reason", risk = riskFor(reason, why),
                        trigger = ActivationPolicy.triggerForWellness(why))
                    emit(UiEvent.WellnessChanged)
                }

                override fun onDismissed(reason: String) {
                    alarm.stop()
                    Notifier.cancel(app, Notifier.ID_WELLNESS)
                    emit(UiEvent.WellnessChanged)
                }
            },
        )
        // אותות תנועה בזמן אירוע פעיל נרשמים ביומן הראיות
        motion.onSignal = { s -> if (IncidentManager.isActive) Evidence.motion(s) }
        lastNotificationIdInit()
    }

    /** תוצאה מ-MotionFusion: ≥2 אותות מאששים -> "הכל בסדר?" (לא SOS ישיר). */
    fun onMotionPrompt(p: MotionFusion.Prompt) {
        if (wellness.startCheckin("motion", p.assessment.promptTextHe())) pendingMotion = p.assessment
    }

    /** risk ל-SOS שנפתח מ"הכל בסדר?": האותות שגרמו לשאלה (+ אותות חדשים בחלון), ותוצאת השאלה. */
    fun riskFor(reason: String, why: String): RiskAssessment {
        val nowMs = System.currentTimeMillis()
        val base = if (reason == "motion") pendingMotion?.signals.orEmpty() else emptyList()
        val signals = (base + motion.recentSignals(nowMs)).distinct()
        pendingMotion = null
        // באין תשובה נוסף האות "no_response" (נכנס לביטחון המשולב)
        return ActivationPolicy.wellnessRisk(signals, why, nowMs)
    }

    private fun lastNotificationIdInit() {
        val last = store.lastNotificationId
        if (last >= 0) ring.resetCursor(last)
    }

    /** לקוח API לפי הכתובת והטוקן הנוכחיים. */
    @Synchronized
    fun api(): ApiClient {
        val url = (UrlPolicy.normalize(store.serverUrl) as? UrlPolicy.Result.Ok)?.baseUrl
            ?: throw IllegalStateException("server url not allowed")
        apiCache?.let { if (it.first == url) return it.second }
        return ApiClient(url, { store.token }).also { apiCache = url to it }
    }

    fun nowSec() = System.currentTimeMillis() / 1000.0

    fun networkAvailable(ctx: Context = app): Boolean {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    fun isOnline() = connectivity.isOnline(nowSec())
}
