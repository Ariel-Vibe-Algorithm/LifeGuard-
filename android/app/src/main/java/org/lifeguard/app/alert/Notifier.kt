package org.lifeguard.app.alert

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.lifeguard.app.R
import org.lifeguard.app.api.IncidentView
import org.lifeguard.app.core.Geo
import org.lifeguard.app.ui.ChatActivity
import org.lifeguard.app.ui.HelperActivity
import org.lifeguard.app.ui.IncomingAlertActivity
import org.lifeguard.app.core.Helpers
import org.lifeguard.app.ui.MainActivity
import org.lifeguard.app.ui.WellnessCheckActivity

/** ערוצי התראות ובניית התראות. */
object Notifier {
    const val CH_SERVICE = "service"
    const val CH_RING = "ring_v1"       // הכל בסדר / בדיקת נסיעה: עדיפות מקסימלית, בלי צליל ערוץ (האזעקה מנוגנת בנפרד)
    // קריאה נכנסת (צלצול): כמו ring_v1 אבל עם רטט חזק בערוץ - ההתראה מגושרת לשעון (Wear OS), ושם אין AlarmPlayer:
    // הרטט של הערוץ הוא מה שמעיר את מי שעונד/ת את השעון. בלי צליל ערוץ (בטלפון האזעקה מנוגנת בנפרד).
    const val CH_RING_ALERT = "ring_alert_v1"
    /** רטט חזק וארוך לצלצול (גם בשעון): 3 פעימות של שנייה */
    val RING_VIBRATION = longArrayOf(0, 1000, 400, 1000, 400, 1000)
    const val CH_ALERTS = "alerts"      // קריאת מתנדב / אזעקת ליווי: עדיפות גבוהה עם צליל
    const val CH_INFO = "info"
    const val CH_CHAT = "chat"
    const val CH_QUIET = "quiet"        // בלי צליל ובלי רטט (בעל/ת אירוע שקט)
    // התראת סכנה מ-AI: עדיפות גבוהה (heads-up), בלי צליל/רטט ערוץ - הצליל והרטט מנוגנים לפי העדפות LifeGuard
    // (HazardRuntime), כי העדפות ערוץ אי אפשר לשנות מהאפליקציה אחרי יצירתו
    const val CH_HAZARD = "hazard_v1"

    const val ID_SERVICE = 1
    const val ID_WELLNESS = 2
    const val ID_OWN_SOS = 3
    const val ID_ESCORT_CHECK = 4
    const val ID_CONFIRM = 5

    fun incidentNotifId(incidentId: String) = 1000 + (incidentId.hashCode() and 0x3FFF)
    /** התראת סכנה אחת לכל "משפחה" (פיצוץ/ירי, קול מצוקה, שבירה, אות) - עדכון חומרה מחליף אותה */
    fun hazardNotifId(family: String) = 60000 + (family.hashCode() and 0xFF)
    fun chatNotifId(incidentId: String) = 20000 + (incidentId.hashCode() and 0x3FFF)
    private var infoSeq = 40000

    fun createChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        fun ch(id: String, nameRes: Int, importance: Int, silent: Boolean, block: NotificationChannel.() -> Unit = {}) {
            val c = NotificationChannel(id, ctx.getString(nameRes), importance)
            if (silent) { c.setSound(null, null); c.enableVibration(false) }
            c.block()
            nm.createNotificationChannel(c)
        }
        ch(CH_SERVICE, R.string.ch_service, NotificationManager.IMPORTANCE_LOW, true) { setShowBadge(false) }
        ch(CH_RING, R.string.ch_ring, NotificationManager.IMPORTANCE_HIGH, true) {
            description = ctx.getString(R.string.ch_ring_desc)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            setBypassDnd(true)   // חל רק אם המשתמש/ת אישר/ה גישה ל"נא לא להפריע"
        }
        ch(CH_RING_ALERT, R.string.ch_ring_alert, NotificationManager.IMPORTANCE_HIGH, true) {
            description = ctx.getString(R.string.ch_ring_alert_desc)
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            setBypassDnd(true)
            enableVibration(true)
            vibrationPattern = RING_VIBRATION
        }
        ch(CH_ALERTS, R.string.ch_alerts, NotificationManager.IMPORTANCE_HIGH, false) {
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        }
        ch(CH_INFO, R.string.ch_info, NotificationManager.IMPORTANCE_DEFAULT, false)
        ch(CH_CHAT, R.string.ch_chat, NotificationManager.IMPORTANCE_HIGH, false)
        ch(CH_QUIET, R.string.ch_quiet, NotificationManager.IMPORTANCE_LOW, true) {
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        }
        ch(CH_HAZARD, R.string.ch_hazard, NotificationManager.IMPORTANCE_HIGH, true) {
            description = ctx.getString(R.string.ch_hazard_desc)
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        }
    }

    private fun canPost(ctx: Context) = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(ctx, android.Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    @Suppress("MissingPermission")
    private fun post(ctx: Context, id: Int, n: Notification) {
        if (canPost(ctx)) runCatching { NotificationManagerCompat.from(ctx).notify(id, n) }
    }

    fun cancel(ctx: Context, id: Int) = NotificationManagerCompat.from(ctx).cancel(id)

    private fun pi(ctx: Context, req: Int, intent: Intent): PendingIntent =
        PendingIntent.getActivity(ctx, req, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun mainIntent(ctx: Context) = pi(ctx, 0, Intent(ctx, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP))

    fun serviceNotification(ctx: Context, text: String): Notification =
        NotificationCompat.Builder(ctx, CH_SERVICE)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(ctx.getString(R.string.service_connected))
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(mainIntent(ctx))
            .build()

    fun updateService(ctx: Context, text: String) = post(ctx, ID_SERVICE, serviceNotification(ctx, text))

    /** קריאה נכנסת: מתנדב/ת תמיכה/הזעקה (my_role=helper) -> מסך הבקשה (בלי שם/מיקום לפני קבלה); אחרת מסך הקריאה. */
    fun alertIntent(ctx: Context, v: IncidentView, ringing: Boolean): Intent =
        (if (v.isHelper) Intent(ctx, HelperActivity::class.java) else Intent(ctx, IncomingAlertActivity::class.java))
            .putExtra(IncomingAlertActivity.EXTRA_INCIDENT_ID, v.id)
            .putExtra(IncomingAlertActivity.EXTRA_RINGING, ringing)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)

    private fun title(ctx: Context, v: IncidentView) =
        if (v.isHelper) Helpers.notificationTitle(v) else ctx.getString(R.string.alert_title, v.ownerDisplay)

    private fun body(v: IncidentView): String {
        if (v.isHelper) return Helpers.notificationText(v)
        val parts = arrayListOf(Geo.emergencyTypeHe(v.emergencyType))
        v.distanceKm?.let { parts += Geo.formatDistanceHe(it) }
        if (v.isSilent) parts += "מצוקה שקטה"
        return parts.joinToString(" · ")
    }

    /**
     * פעולות לשעון (WearableExtender - מוצגות רק בשעון; בטלפון יש את מסך הקריאה):
     * "קבלה" (רק קריאה רגילה - בקשת מתנדב/ת תמיכה/הזעקה נפתחת במסך שלה) -> AlertActionReceiver בטלפון, ו"פתיחה בטלפון".
     */
    private fun wearActions(ctx: Context, v: IncidentView, open: PendingIntent): NotificationCompat.WearableExtender {
        val ext = NotificationCompat.WearableExtender()
        if (!v.isHelper) {
            val accept = PendingIntent.getBroadcast(ctx, incidentNotifId(v.id),
                Intent(ctx, AlertActionReceiver::class.java).setAction(AlertActionReceiver.ACTION_ACCEPT)
                    .putExtra(AlertActionReceiver.EXTRA_INCIDENT_ID, v.id)
                    .putExtra(AlertActionReceiver.EXTRA_OWNER, v.ownerDisplay),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            ext.addAction(NotificationCompat.Action.Builder(R.drawable.ic_shield, ctx.getString(R.string.watch_action_accept), accept).build())
        }
        ext.addAction(NotificationCompat.Action.Builder(R.drawable.ic_shield, ctx.getString(R.string.watch_action_open_phone), open).build())
        return ext
    }

    /**
     * צלצול: התראה במסך מלא (fullScreenIntent) על ערוץ בעדיפות מקסימלית.
     * לא ongoing: Wear OS לא מגשר התראות ongoing לשעון. הצלצול עצמו (AlarmPlayer + FLAG_INSISTENT) נמשך עד תגובה /
     * "השתק" / ring_stopped, גם אם ההתראה הוחלקה.
     */
    fun showRing(ctx: Context, v: IncidentView) {
        val full = pi(ctx, incidentNotifId(v.id), alertIntent(ctx, v, true))
        val n = NotificationCompat.Builder(ctx, CH_RING_ALERT)
            .setSmallIcon(R.drawable.ic_shield)
            .setColor(ContextCompat.getColor(ctx, R.color.sos_red))
            .setContentTitle((if (v.isHelper) "" else "🚨 ") + title(ctx, v))
            .setContentText(body(v))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(false)
            .setAutoCancel(false)
            .setVibrate(RING_VIBRATION)          // לפני Android 8 (אין ערוצים)
            .setFullScreenIntent(full, true)
            .setContentIntent(full)
            .extend(wearActions(ctx, v, full))
            .build()
        n.flags = n.flags or Notification.FLAG_INSISTENT
        post(ctx, incidentNotifId(v.id), n)
    }

    /** קריאה בלי צלצול (מישהו כבר מטפל / מתנדב / הצלצול נעצר). */
    fun showIncidentAlert(ctx: Context, v: IncidentView, loud: Boolean, note: String? = null) {
        val open = pi(ctx, incidentNotifId(v.id), alertIntent(ctx, v, false))
        val n = NotificationCompat.Builder(ctx, if (loud) CH_ALERTS else CH_INFO)
            .setSmallIcon(R.drawable.ic_shield)
            .setColor(ContextCompat.getColor(ctx, R.color.sos_red))
            .setContentTitle(title(ctx, v))
            .setContentText(note ?: body(v))
            .setStyle(NotificationCompat.BigTextStyle().bigText(listOfNotNull(note, body(v)).joinToString("\n")))
            .setPriority(if (loud) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setAutoCancel(true)
            .setContentIntent(open)
            .extend(wearActions(ctx, v, open))
            .build()
        post(ctx, incidentNotifId(v.id), n)
    }

    /** quiet = בעל/ת אירוע במצוקה שקטה: טקסט ניטרלי, בלי צליל/רטט/הצצה. */
    fun showChat(ctx: Context, incidentId: String, from: String, quiet: Boolean) {
        val open = pi(ctx, chatNotifId(incidentId), ChatActivity.intent(ctx, incidentId))
        val b = NotificationCompat.Builder(ctx, if (quiet) CH_QUIET else CH_CHAT)
            .setSmallIcon(R.drawable.ic_shield)
            .setAutoCancel(true)
            .setContentIntent(open)
        if (quiet) {
            b.setContentTitle("LifeGuard").setContentText("עדכון").setSilent(true)
                .setPriority(NotificationCompat.PRIORITY_LOW).setVisibility(NotificationCompat.VISIBILITY_SECRET)
        } else {
            b.setContentTitle(ctx.getString(R.string.chat_title)).setContentText("הודעה חדשה מ$from")
                .setPriority(NotificationCompat.PRIORITY_HIGH).setCategory(NotificationCompat.CATEGORY_MESSAGE)
        }
        post(ctx, chatNotifId(incidentId), b.build())
    }

    fun showInfo(ctx: Context, title: String, text: String, quiet: Boolean, incidentId: String? = null, loud: Boolean = false) {
        val intent = if (incidentId != null) ChatActivity.intent(ctx, incidentId) else Intent(ctx, MainActivity::class.java)
        val id = synchronized(this) { infoSeq = if (infoSeq > 49000) 40000 else infoSeq + 1; infoSeq }
        val n = NotificationCompat.Builder(ctx, when { quiet -> CH_QUIET; loud -> CH_ALERTS; else -> CH_INFO })
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setSilent(quiet)
            .setContentIntent(pi(ctx, id, intent))
            .build()
        post(ctx, id, n)
    }

    /** "הכל בסדר?" של נסיעה. silent: כותרת ניטרלית, בלי צבע אדום ובלי צליל. */
    fun showEscortCheck(ctx: Context, text: String, silent: Boolean) {
        val full = pi(ctx, ID_ESCORT_CHECK, WellnessCheckActivity.escortIntent(ctx))
        val n = NotificationCompat.Builder(ctx, CH_RING)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(if (silent) "LifeGuard" else ctx.getString(R.string.ride_check_title))
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(if (silent) NotificationCompat.CATEGORY_REMINDER else NotificationCompat.CATEGORY_ALARM)
            .setVisibility(if (silent) NotificationCompat.VISIBILITY_PRIVATE else NotificationCompat.VISIBILITY_PUBLIC)
            .setSilent(true)
            .setOngoing(true)
            .setFullScreenIntent(full, true)
            .setContentIntent(full)
            .build()
        post(ctx, ID_ESCORT_CHECK, n)
    }

    fun showWellness(ctx: Context, reasonText: String) {
        val full = pi(ctx, ID_WELLNESS, Intent(ctx, WellnessCheckActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        val n = NotificationCompat.Builder(ctx, CH_RING)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(ctx.getString(R.string.wellness_title))
            .setContentText(reasonText)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setFullScreenIntent(full, true)
            .setContentIntent(full)
            .build()
        post(ctx, ID_WELLNESS, n)
    }

    /** זיהוי אוטומטי שהשרת סימן "unconfirmed": לבקש מהבעלים לבחור "זה חירום" / "אני בסדר" (במסך הראשי). */
    fun showConfirmEmergency(ctx: Context) {
        val open = mainIntent(ctx)
        val n = NotificationCompat.Builder(ctx, CH_ALERTS)
            .setSmallIcon(R.drawable.ic_shield)
            .setColor(ContextCompat.getColor(ctx, R.color.sos_red))
            .setContentTitle(ctx.getString(R.string.confirm_title))
            .setContentText(ctx.getString(R.string.confirm_text))
            .setStyle(NotificationCompat.BigTextStyle().bigText(ctx.getString(R.string.confirm_text)))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        post(ctx, ID_CONFIRM, n)
    }

    /**
     * "LifeGuard זיהה סכנה אפשרית": heads-up שפותח את HazardAlertActivity (סוג, חומרה, ביטחון, הסבר, פעולות).
     * חומרה גבוהה - גם fullScreenIntent (מסך נעול/כבוי). בלי כפתורי פעולה בהתראה עצמה: כל פעולה משמעותית דורשת
     * את המסך ואישור מפורש. לא נקרא בכלל במצוקה שקטה / SOS פעיל (HazardGate).
     */
    fun showHazard(ctx: Context, a: org.lifeguard.app.hazard.HazardAlert, p: org.lifeguard.app.hazard.HazardPrefs, updated: Boolean) {
        val s = org.lifeguard.app.hazard.HazardText.of(p.language)
        val id = hazardNotifId(a.family)
        val open = pi(ctx, id, org.lifeguard.app.ui.HazardAlertActivity.intent(ctx, a.id))
        val pct = org.lifeguard.app.hazard.AudioIntel.fmt(a.confidence * 100, 0)
        val text = "${s.label(a)} · ${s.severity}: ${s.sevLabel(a.severity)} · ${s.confidence}: $pct%"
        val big = listOfNotNull(text, s.explanation(a).ifEmpty { null }, s.notVerified,
            if (updated || a.count > 1) "${s.updated} (×${a.count})" else null).joinToString("\n")
        val color = when (a.severity) {
            "high" -> R.color.lg_fill_sos; "medium" -> R.color.lg_fill_warning; "low" -> R.color.lg_fill_accent
            else -> R.color.lg_fill_neutral
        }
        val b = NotificationCompat.Builder(ctx, CH_HAZARD)
            .setSmallIcon(R.drawable.ic_shield)
            .setColor(ContextCompat.getColor(ctx, color))
            .setContentTitle("⚠️ " + s.title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(big))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(open)
            .addAction(NotificationCompat.Action.Builder(R.drawable.ic_shield, s.openApp, open).build())
        if (a.severity == "high") b.setFullScreenIntent(open, true)
        post(ctx, id, b.build())
    }

    fun cancelHazard(ctx: Context, family: String) = cancel(ctx, hazardNotifId(family))
}
