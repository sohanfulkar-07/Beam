# PhotoBeam ProGuard / R8 Rules

# Preserve application classes and public members
-keep class com.photobeam.app.** { *; }
-keepattributes *Annotation*
-keepattributes SourceFile,LineNumberTable

# BouncyCastle TLS & Crypto
-keep class org.bouncycastle.** { *; }
-dontwarn org.bouncycastle.**

# Kotlin Coroutines
-keepclassmembers class kotlinx.coroutines.** { *; }
-dontwarn kotlinx.coroutines.**

# Okio streaming I/O
-dontwarn okio.**
-keep class okio.** { *; }

# ZXing QR Code Generation & Scanning
-keep class com.google.zxing.** { *; }
-dontwarn com.google.zxing.**
-keep class com.journeyapps.barcodescanner.** { *; }
-dontwarn com.journeyapps.barcodescanner.**

# CameraX
-keep class androidx.camera.** { *; }
-dontwarn androidx.camera.**

# ML Kit Barcode Scanning
-keep class com.google.mlkit.vision.barcode.** { *; }
-dontwarn com.google.mlkit.vision.barcode.**
-keep class com.google.android.gms.vision.** { *; }
-dontwarn com.google.android.gms.vision.**

# JSON
-keep class org.json.** { *; }
-dontwarn org.json.**
