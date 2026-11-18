plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

fun setting(name: String): String = "\"${providers.gradleProperty(name).get()}\""

android {
    namespace = "dev.smarthome.watch"
    compileSdk = 34

    defaultConfig {
        applicationId = "dev.smarthome.watch"
        minSdk = 30 // Wear OS 3
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField("String", "API_URL", setting("smarthome.apiUrl"))
        buildConfigField("String", "COGNITO_REGION", setting("smarthome.cognitoRegion"))
        buildConfigField("String", "COGNITO_CLIENT_ID", setting("smarthome.cognitoClientId"))
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.wear.compose:compose-material:1.4.0")
    implementation("androidx.wear.compose:compose-foundation:1.4.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
