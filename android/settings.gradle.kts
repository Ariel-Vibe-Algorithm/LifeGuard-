pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "LifeGuard"
include(":app")
// לוגיקה משותפת לטלפון ולשעון (זיהוי נפילה, הערכת סיכון, פרוטוקול שעון<->טלפון)
include(":shared")
// אפליקציית Wear OS (Galaxy Watch 4+, Pixel Watch) - אותו applicationId כמו הטלפון (חובה ל-Data Layer)
include(":wear")
