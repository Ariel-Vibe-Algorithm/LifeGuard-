// אפליקציית Wear OS (Galaxy Watch 4+ / Pixel Watch): SOS בהחזקה, זיהוי נפילה על פרק היד, Tile.
// applicationId זהה לטלפון (org.lifeguard.app) - חובה כדי שה-Wearable Data Layer יחבר ביניהן (וגם אותה חתימה).
// לא עצמאית (standalone=false במניפסט): השעון לא פונה לשרת בעצמו - כל SOS עובר דרך אפליקציית הטלפון.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.lifeguard.wear"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "org.lifeguard.app"
        minSdk = 30            // Wear OS 3+ (Galaxy Watch 4 ומעלה, Pixel Watch)
        targetSdk = 35
        // מספר גרסה נפרד מהטלפון (Play דורש מספר שונה לכל APK באותו applicationId); versionName זהה
        versionCode = 1_000_002
        versionName = "0.2.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
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
    // אותו זיהוי נפילה (FallDetector / MotionFusion) ואותו פרוטוקול כמו בטלפון
    implementation(project(":shared"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("com.google.android.gms:play-services-wearable:18.2.0")
    // Ongoing Activity: שירות זיהוי הנפילה מוצג בשעון כפעילות מתמשכת (סמל בפני השעון)
    implementation("androidx.wear:wear-ongoing:1.0.0")
    // Tile עם קיצור דרך למסך ה-SOS
    implementation("androidx.wear.tiles:tiles:1.4.1")
    implementation("androidx.wear.protolayout:protolayout:1.2.1")
    implementation("androidx.concurrent:concurrent-futures:1.2.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
