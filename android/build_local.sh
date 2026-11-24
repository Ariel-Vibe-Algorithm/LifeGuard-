#!/usr/bin/env bash
# בנייה עם הכלים המקומיים שב-.tools/ (Git Bash). שימוש: android/build_local.sh testDebugUnitTest assembleDebug
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$(dirname "$HERE")/.tools"
JDK="$(ls -d "$TOOLS"/jdk-17* | head -1)"
export JAVA_HOME="$JDK"
export ANDROID_HOME="$TOOLS/android-sdk" ANDROID_SDK_ROOT="$TOOLS/android-sdk"
export GRADLE_USER_HOME="$TOOLS/gradle-home" ANDROID_USER_HOME="$TOOLS/android-user-home"
export PATH="$JAVA_HOME/bin:$PATH"
cd "$HERE"
exec ./gradlew --no-daemon "$@"
