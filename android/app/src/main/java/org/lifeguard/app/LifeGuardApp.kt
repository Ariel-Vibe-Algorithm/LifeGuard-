package org.lifeguard.app

import android.app.Application
import org.lifeguard.app.alert.Notifier
import org.lifeguard.app.fcm.FcmManager

class LifeGuardApp : Application() {
    override fun onCreate() {
        super.onCreate()
        LG.init(this)
        Notifier.createChannels(this)
        // השכמה דרך FCM - רק אם יש firebase.properties בבנייה; אחרת "FCM לא מוגדר" והכל עובד דרך long-poll
        FcmManager.init(this)
    }
}
