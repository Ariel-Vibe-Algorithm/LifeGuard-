import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// ---- FCM (השכמה) - רשות. בלי android/firebase.properties הבנייה מצליחה והאפליקציה מציגה "FCM לא מוגדר".
// בכוונה בלי התוסף google-services ובלי google-services.json: Firebase מאותחל ידנית מ-BuildConfig (FcmManager).
val fcmProps = Properties().apply {
    val f = rootProject.file("firebase.properties")
    if (f.isFile) f.reader(Charsets.UTF_8).use { load(it) }
}
fun fcmValue(key: String): String = (fcmProps.getProperty(key) ?: "").trim()
fun javaString(v: String): String = "\"" + v.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
val fcmKeys = listOf("fcm.apiKey", "fcm.appId", "fcm.projectId", "fcm.senderId")
val fcmConfigured = fcmKeys.all { k -> fcmValue(k).let { it.isNotEmpty() && !it.contains("REPLACE", ignoreCase = true) } }
if (!fcmConfigured) {
    logger.lifecycle("LifeGuard: FCM not configured (no complete android/firebase.properties) - building without FCM wake-up")
}

// ---- עזרה ראשונה אופליין: first_aid/ במאגר הוא מקור האמת היחיד. בזמן הבנייה התוכן (מדריכי בסיס, חבילות הרחבה +
// active.json, synonyms.json, supplement_policy.json) מועתק לתיקיית assets שנוצרת ב-build/ (לא נכנסת ל-git) - אף פעם לא ידנית.
// (-Plifeguard.firstAidDir=<path> - רק לבדיקת מסלול הכישלון; ברירת המחדל היא תמיד ../first_aid)
val firstAidRoot = (findProperty("lifeguard.firstAidDir") as String?)?.let { file(it) } ?: rootProject.file("../first_aid")
val firstAidIncludes = listOf("content/**", "synonyms.json", "supplement_policy.json")
val firstAidExcludes = listOf("**/__pycache__/**", "**/*.py", "**/*.pyc", "**/*.tmp")

abstract class BundleFirstAidContent : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val content: ConfigurableFileCollection

    @get:Input
    abstract val sourceRoot: Property<String>

    @get:Input
    abstract val includes: ListProperty<String>

    @get:Input
    abstract val excludes: ListProperty<String>

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @get:Inject
    abstract val fs: FileSystemOperations

    @TaskAction
    fun bundle() {
        val root = File(sourceRoot.get())
        val guides = File(root, "content/first_aid_guides_he.json")
        if (!guides.isFile) {
            throw GradleException("First aid content missing: $guides not found. The app bundles first_aid/ from the " +
                "repository at build time (single source of truth) - restore first_aid/content/ before building.")
        }
        val inc = includes.get()
        val exc = excludes.get()
        fs.sync {
            from(root) { include(inc); exclude(exc) }
            into(outputDir.dir("first_aid"))
        }
    }
}

val bundleFirstAidContent = tasks.register<BundleFirstAidContent>("bundleFirstAidContent") {
    description = "Copies ../first_aid content into generated assets (assets/first_aid/...)"
    sourceRoot.set(firstAidRoot.absolutePath)
    includes.set(firstAidIncludes)
    excludes.set(firstAidExcludes)
    content.from(fileTree(firstAidRoot) { include(firstAidIncludes); exclude(firstAidExcludes) })
}

// ---- מקומות בטוחים אופליין: safe_places/safe_places_il.json במאגר (tools/build_safe_places.py) הוא מקור האמת.
// מועתק בזמן הבנייה ל-assets/safe_places/ (תיקייה שנוצרת ב-build/) - כמו העזרה הראשונה, אף פעם לא ידנית.
val safePlacesFile = rootProject.file("../safe_places/safe_places_il.json")

abstract class BundleSafePlaces : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val source: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @get:Inject
    abstract val fs: FileSystemOperations

    @TaskAction
    fun bundle() {
        fs.sync {
            from(source)
            into(outputDir.dir("safe_places"))
        }
    }
}

val bundleSafePlaces = tasks.register<BundleSafePlaces>("bundleSafePlaces") {
    description = "Copies ../safe_places/safe_places_il.json into generated assets (assets/safe_places/...)"
    source.set(safePlacesFile)
}

// ---- זיהוי סכנה קולי במכשיר: YAMNet (Apache-2.0, research/models/yamnet/yamnet_float_embeddings.tflite, ~16MB) מועתק בזמן
// הבנייה ל-assets/hazard/ (תיקייה שנוצרת ב-build/). research/models/ לא במאגר - בלי הקובץ הבנייה מצליחה, והאפליקציה
// אומרת בגלוי שהזיהוי לא זמין (HazardRuntime). הראשים / הספים: assets/hazard/hazard_models.* (tools/build_android_hazard_assets.py).
val yamnetModel = rootProject.file("../research/models/yamnet/yamnet_float_embeddings.tflite")

abstract class BundleYamnet : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val source: ConfigurableFileCollection

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @get:Inject
    abstract val fs: FileSystemOperations

    @TaskAction
    fun bundle() {
        val present = source.files.filter { it.isFile }
        if (present.isEmpty()) {
            logger.warn("LifeGuard: YAMNet model not found (research/models/yamnet/yamnet_float_embeddings.tflite) - " +
                "building WITHOUT on-device hazard detection (the app says so in Settings)")
        }
        fs.sync {
            from(present)
            into(outputDir.dir("hazard"))
        }
    }
}

val bundleYamnet = tasks.register<BundleYamnet>("bundleYamnet") {
    description = "Copies ../research/models/yamnet/yamnet_float_embeddings.tflite into generated assets (assets/hazard/...)"
    source.from(yamnetModel)
}

android {
    namespace = "org.lifeguard.app"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "org.lifeguard.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.2.0"
        // כתובת ברירת מחדל: השרת המקומי מהאמולטור (10.0.2.2 = המחשב המארח). ניתן לשנות במסך ההתחברות.
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"http://10.0.2.2:8765\"")
        // הגדרת Firebase (ריק כשאין firebase.properties). אלה מזהים ציבוריים של האפליקציה, לא סודות שרת.
        buildConfigField("boolean", "FCM_CONFIGURED", fcmConfigured.toString())
        buildConfigField("String", "FCM_API_KEY", javaString(if (fcmConfigured) fcmValue("fcm.apiKey") else ""))
        buildConfigField("String", "FCM_APP_ID", javaString(if (fcmConfigured) fcmValue("fcm.appId") else ""))
        buildConfigField("String", "FCM_PROJECT_ID", javaString(if (fcmConfigured) fcmValue("fcm.projectId") else ""))
        buildConfigField("String", "FCM_SENDER_ID", javaString(if (fcmConfigured) fcmValue("fcm.senderId") else ""))
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    // מודל TFLite נטען ב-memory map ישירות מה-APK - חייב להישמר לא דחוס
    androidResources {
        noCompress += "tflite"
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
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
            // בדיקת אינטגרציה מול השרת האמיתי רצה רק כשמוגדר LIFEGUARD_IT_URL (ראו README_HE.md)
            it.systemProperty("lifeguard.it.url", System.getenv("LIFEGUARD_IT_URL") ?: "")
            // בדיקות העזרה הראשונה (כולל התאימות ל-Python) קוראות את אותו תוכן ישירות מ-first_aid/ במאגר
            it.systemProperty("lifeguard.firstaid.dir", firstAidRoot.absolutePath)
            // בדיקת התאימות של המקומות הבטוחים קוראת את אותה רשימה ישירות מ-safe_places/ במאגר
            it.systemProperty("lifeguard.safeplaces.file", safePlacesFile.absolutePath)
            it.inputs.file(safePlacesFile).withPropertyName("safePlaces").withPathSensitivity(PathSensitivity.NONE)
            // בדיקות התאימות של מנוע הסכנה קוראות את אותם נכסים שנארזים באפליקציה
            it.systemProperty("lifeguard.hazard.assets", file("src/main/assets/hazard").absolutePath)
            it.inputs.files(fileTree(firstAidRoot) { include(firstAidIncludes); exclude(firstAidExcludes) })
                .withPropertyName("firstAidContent").withPathSensitivity(PathSensitivity.RELATIVE)
            it.testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
        }
    }
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(bundleFirstAidContent, BundleFirstAidContent::outputDir)
        variant.sources.assets?.addGeneratedSourceDirectory(bundleSafePlaces, BundleSafePlaces::outputDir)
        variant.sources.assets?.addGeneratedSourceDirectory(bundleYamnet, BundleYamnet::outputDir)
    }
}

dependencies {
    // זיהוי נפילה / מיזוג חיישנים / הערכת סיכון + פרוטוקול השעון - משותפים עם אפליקציית השעון (:wear)
    implementation(project(":shared"))
    // Wearable Data Layer: קבלת SOS / נפילה מהשעון ושליחת אישור + סטטוס אמיתי בחזרה (WearSosListenerService)
    implementation("com.google.android.gms:play-services-wearable:18.2.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // FCM - השכמה בלבד (BoM 33.7.0, דצמבר 2024; בלי התוסף google-services)
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-messaging")
    // משיכה חד-פעמית אחרי השכמה כשאסור להפעיל שירות חזית (Android 12+)
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    // זיהוי סכנה קולי במכשיר (YAMNet): LiteRT = TensorFlow Lite (Apache-2.0), יוני 2025, תומך בדפי 16KB
    implementation("com.google.ai.edge.litert:litert:1.4.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    // org.json אמיתי לבדיקות JVM (ב-android.jar יש רק stubs)
    testImplementation("org.json:json:20240303")
}
