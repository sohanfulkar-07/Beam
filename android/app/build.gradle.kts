import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.photobeam.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.photobeam.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 2
        versionName = "1.0.0-beta.1"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        create("release") {
            val releasePropFile = rootProject.file("release.properties")
            val releaseProps = Properties()
            if (releasePropFile.exists()) {
                releasePropFile.inputStream().use { releaseProps.load(it) }
            }

            val ksFile = System.getenv("PHOTOBEAM_KEYSTORE_FILE")
                ?: releaseProps.getProperty("photobeam.keystore.file")
                ?: "release-local.keystore"
            val ksPass = System.getenv("PHOTOBEAM_KEYSTORE_PASSWORD")
                ?: releaseProps.getProperty("photobeam.keystore.password")
                ?: ""
            val kAlias = System.getenv("PHOTOBEAM_KEY_ALIAS")
                ?: releaseProps.getProperty("photobeam.key.alias")
                ?: ""
            val kPass = System.getenv("PHOTOBEAM_KEY_PASSWORD")
                ?: releaseProps.getProperty("photobeam.key.password")
                ?: ""

            val storeFileTarget = file(ksFile)
            if (storeFileTarget.exists() && ksPass.isNotEmpty()) {
                storeFile = storeFileTarget
                storePassword = ksPass
                keyAlias = kAlias
                keyPassword = kPass
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            val relSigning = signingConfigs.getByName("release")
            if (relSigning.storeFile != null) {
                signingConfig = relSigning
            }
        }
        debug {
            isDebuggable = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = "11"
    }

    buildFeatures {
        compose = true
    }

    // Required for zxing-android-embedded
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
            excludes += "META-INF/versions/**"
            excludes += "/META-INF/versions/**"
            excludes += "META-INF/INDEX.LIST"
            excludes += "/META-INF/INDEX.LIST"
            excludes += "META-INF/io.netty.versions.properties"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.navigation.compose)

    // QR generation
    implementation(libs.zxing.android.embedded)

    // Camera / QR scanning
    implementation(libs.androidx.camera.core)
    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.mlkit.barcode.scanning)

    // Coroutines
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.coroutines.core)

    // TLS cert generation (BouncyCastle)
    implementation(libs.bouncycastle.bcpkix)

    // Security crypto for encrypted prefs
    implementation(libs.androidx.security.crypto)

    // Streaming I/O
    implementation(libs.okio)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.core)
    testImplementation("org.json:json:20240303")
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.ui.test.junit4)
    debugImplementation(libs.androidx.ui.tooling)
    debugImplementation(libs.androidx.ui.test.manifest)
}
