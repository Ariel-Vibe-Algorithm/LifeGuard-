// לוגיקה טהורה (Kotlin בלי Android API) שמשותפת לטלפון (:app) ולשעון (:wear):
// זיהוי נפילה / מיזוג חיישנים / הערכת סיכון (הועברו מ-app בלי שינוי) + פרוטוקול ה-Data Layer בין השעון לטלפון.
// ספריית Android (ולא JVM) רק כדי ש-org.json יגיע מהפלטפורמה כמו קודם; בבדיקות JVM - org.json אמיתי.
plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.lifeguard.shared"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
        unitTests.all {
            it.testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
        }
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
