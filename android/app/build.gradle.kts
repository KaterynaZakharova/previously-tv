plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    namespace = "com.previously.tv"
    compileSdk = 35
    defaultConfig { applicationId = "com.previously.tv"; minSdk = 25; targetSdk = 35; versionCode = 2; versionName = "2.0" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
}
