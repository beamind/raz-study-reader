# Android third-party components

The Android application uses these families of libraries under Apache License 2.0:

- AndroidX libraries — The Android Open Source Project: https://android.googlesource.com/platform/frameworks/support/
- Apache Cordova Android — The Apache Software Foundation: https://github.com/apache/cordova-android
- Kotlin standard library and kotlinx.coroutines — JetBrains s.r.o. and Kotlin contributors: https://github.com/JetBrains/kotlin and https://github.com/Kotlin/kotlinx.coroutines
- JSpecify annotations — JSpecify contributors: https://github.com/jspecify/jspecify
- Guava's ListenableFuture interface and Error Prone annotations, if resolved transitively — The Guava Authors / The Error Prone Authors: https://github.com/google/guava and https://github.com/google/error-prone

The full Apache License 2.0 is provided in `licenses/Apache-2.0.txt` and bundled into the reader's component notices. Capacitor and the JavaScript libraries retain their individual notices, collected directly from their installed packages by `tools/build_third_party_notices.py`.

Review `:app:dependencies --configuration releaseRuntimeClasspath` when changing native dependencies, and add any new license families or upstream notices before distributing a release. The release verification report records the resolved native dependency list.
