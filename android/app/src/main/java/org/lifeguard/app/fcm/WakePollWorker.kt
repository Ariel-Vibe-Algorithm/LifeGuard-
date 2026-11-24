package org.lifeguard.app.fcm

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.lifeguard.app.LG
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.api.ApiException
import org.lifeguard.app.service.LifeGuardService
import org.lifeguard.app.service.NotificationHandler
import java.io.IOException

/**
 * משיכה חד-פעמית של /v1/notifications (wait=0) אחרי השכמת FCM, כשאנדרואיד לא מאפשר להפעיל את שירות החזית.
 * ההתראות עוברות באותה מכונת צלצול / התראות כמו בשירות. אחר כך מנסים שוב להפעיל את השירות (אם מותר).
 */
class WakePollWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        if (!LG.store.isLoggedIn) return@withContext Result.success()
        try {
            NotificationHandler.pollOnce(applicationContext)
            LifeGuardService.start(applicationContext)          // runCatching בפנים - ייתכן שעדיין אסור
            Result.success()
        } catch (e: ApiException) {
            if (e.isRetryable && runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.success()
        } catch (e: IOException) {
            if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else Result.success()
        } catch (e: IllegalStateException) {
            Log.w(TAG, "wake poll: ${e.javaClass.simpleName}")
            Result.success()
        }
    }

    /** expedited ב-API < 31 רץ כשירות חזית של WorkManager - צריך התראה (טקסט ניטרלי, בלי תוכן מההשכמה). */
    override suspend fun getForegroundInfo(): ForegroundInfo =
        ForegroundInfo(NOTIFICATION_ID, Notifier.serviceNotification(applicationContext, "LifeGuard בודק קריאות חדשות"))

    companion object {
        private const val TAG = "LifeGuardFcm"
        private const val NAME = "lifeguard_wake_poll"
        private const val MAX_ATTEMPTS = 3
        private const val NOTIFICATION_ID = 6

        fun enqueue(ctx: Context, expedited: Boolean) {
            val req = OneTimeWorkRequestBuilder<WakePollWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .apply { if (expedited) setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST) }
                .build()
            WorkManager.getInstance(ctx).enqueueUniqueWork(NAME, ExistingWorkPolicy.KEEP, req)
        }
    }
}
