// Top-level build file. Versions are pinned in gradle/libs.versions.toml.
// AGP 9 compiles Kotlin itself ("built-in Kotlin"), so there is no kotlin-android plugin here;
// the Compose and serialization compiler plugins pin the Kotlin version on the build classpath.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
