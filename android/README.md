# Invoice Capture (Android)

The phone half of the invoice pipeline. A store manager photographs a supplier delivery's
invoices with the phone; the photos upload over the office Wi-Fi to the always-on store laptop
(`../pipeline`), which reads them. The manager then checks the results on the phone, fixes
what the laptop could not read, and submits the batch; the laptop produces the input report (PDF + CSV).

The phone and the laptop follow one contract: `../pipeline/docs/phone-api.md` (v1).

## What the app does

- **Pairing** – scan the QR code on the portal's `/pair` page, or type the laptop address and the
  6-character code. The app calls `POST /api/v1/pair` and keeps the laptop address, device token and
  shop in app-private storage.
- **Home** – batches for this shop (`GET /api/v1/batches`), the upload queue on this phone, a large
  **Scan invoices** button and **Settings** (laptop address, device name, check connection, unpair).
- **Capture** – one Google ML Kit Document Scanner session is one batch (basic mode, photos kept as
  taken, JPEG, no gallery import, up to 100 pages). If that scanner cannot open, the app offers the
  phone's camera instead. Photograph one page, use **Add page** for the rest and **Done** to upload
  them as one batch. Camera mode keeps the full photograph without automatic cropping; keep the
  whole invoice in view and avoid shadows. A retake returns one photograph directly. For each page,
  in capture order, the app runs the §6
  quality checks (blur, too dark / washed out, faint print, glare) on an 800 px grayscale copy, makes the
  upload copy (orientation applied, 3000 px long side, JPEG quality 90), stores it in app-private
  storage, hashes it (SHA-256) and queues a WorkManager upload (`PUT …/photos/{client_photo_key}` with
  `X-Photo-Ordinal`, `X-Content-SHA256`, `X-Quality`). Quality problems are shown straight after the
  scan, but every page still uploads. The phone deletes its copy **only** after a 200/201 receipt whose
  `batch_id`, `client_photo_key`, `sha256` and `bytes` all match; anything else keeps the file and
  retries with exponential backoff. `409 key_reused` and `422 hash_mismatch` stop that page and are shown
  with **Try again** and **Discard**. Discarding (after a confirmation) deletes the phone's copy and drops
  the page from the queue, so no later retry can send it; that invoice stays on paper and the rest of the
  batch carries on. `POST …/complete` is sent once every remaining page is uploaded.
- **Batch** – tabs **Good / Corrections / Retake / Duplicates** with counts (Corrections in red).
  Reading progress is polled every 3 s while the batch is uploading or being read and every 30 s
  otherwise, with `ETag`/`If-None-Match`. Swipe between the invoices of a tab. Each invoice shows its
  photo(s) with pinch-zoom and page badges, and a panel (sliding up over the lower screen on a phone,
  beside the photo on a wide screen) with supplier, invoice number, date, stated totals against the
  lines sum, the reasons for the tab, and the lines with starred lines first.
  - Tap a line to change it: search the shop's stock key, quantity as printed and cost, or free text when
    the item is not in the key (`PUT`/`POST …/lines`), or remove a line the laptop invented (`DELETE`).
    **Search stock** on every panel finds an item and applies it to any line, flagged or not.
  - **Edit** changes supplier, invoice number and date (`PATCH`).
  - Retake tab: **Recapture page N** opens the scanner for one page and uploads it with `X-Replaces-Photo`.
  - Duplicates tab: compare this invoice with the one it repeats — fetched with
    `GET /api/v1/invoices/{invoice_id}` when it is not already loaded, so an older batch is found too;
    **Delete duplicate** (after a confirmation) or **Not a duplicate**.
  - **Submit batch** stays disabled, with the reason, while photos are uploading or being read; on success
    the app says the report is ready on the laptop.
- **Resilience** – uploads survive the app closing and the phone restarting (WorkManager), wait for a
  network, and restart by themselves when the laptop answers again. Screens keep their state across
  rotation and process death (ViewModel + SavedStateHandle). When the laptop is unreachable the app says
  "Waiting for the laptop" and keeps showing what was last loaded.

## Versions

| | |
|---|---|
| Android Gradle Plugin | 9.4.0 (built-in Kotlin) |
| Gradle (wrapper) | 9.6.1 |
| Kotlin | 2.4.10 (Compose and serialization compiler plugins) |
| JDK for Gradle | 21 (Zulu 21 on the development laptop) |
| compileSdk / targetSdk / minSdk | 37 / 37 / 29 (Android 10) |
| Compose BOM | 2026.09.00 (Material 3) |
| Libraries | Navigation Compose 2.10.1, Lifecycle 2.11.0, WorkManager 2.11.2, DataStore 1.2.1, kotlinx.serialization 1.11.0, OkHttp 5.5.0, Coil 3.6.2, ML Kit Document Scanner 16.0.0, Google Code Scanner 16.1.0 |

All versions are pinned in `gradle/libs.versions.toml`.

## Build and test

Gradle needs a JDK that AGP 9 supports; use JDK 21 (Android Studio's bundled JBR also works).
`local.properties` points at the Android SDK (`sdk.dir=…\\AppData\\Local\\Android\\Sdk`); the SDK needs
the `android-37` platform and build-tools 36.

PowerShell, in this folder:

```powershell
$env:JAVA_HOME = 'C:\Program Files\Zulu\zulu-21'
.\gradlew.bat assembleDebug          # builds app\build\outputs\apk\debug\app-debug.apk
.\gradlew.bat testDebugUnitTest      # JVM unit tests; report in app\build\reports\tests\testDebugUnitTest
```

Command Prompt:

```bat
set JAVA_HOME=C:\Program Files\Zulu\zulu-21
gradlew.bat assembleDebug testDebugUnitTest
```

The unit tests cover the pure logic: JSON parsing of the contract, pairing codes and QR parsing, quality
checks and image sizing, receipt verification and the upload policy, tab ordering, poll intervals and
the submit gate, line and header editor validation, pager selection after edits, totals, duplicate
actions, the duplicate lookup's decision (local copy, fetch, refetch on 409), the supplier display name,
the capture summary, and discarding a stuck page from the pending store.

## Open in Android Studio

**File → Open…** and choose this `android` folder (not the parent). Let the Gradle sync
finish. If Studio asks for a Gradle JDK, pick JDK 21 (**Settings → Build, Execution, Deployment → Build
Tools → Gradle → Gradle JDK**). Run the `app` configuration on a connected phone.

## Install on a Samsung phone

1. On the phone: **Settings → About phone → Software information**, tap **Build number** seven times to
   turn on Developer options.
2. **Settings → Developer options → USB debugging** on.
3. Connect the phone by USB and accept the "Allow USB debugging?" prompt on the phone.
4. On the laptop, in this folder:

   ```powershell
   & "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" devices
   & "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r app\build\outputs\apk\debug\app-debug.apk
   ```

   (With `platform-tools` on the PATH this is simply `adb install -r app\build\outputs\apk\debug\app-debug.apk`.)

Google Play services downloads the document and QR scanner features on first use. If the document
scanner is unavailable, camera capture works through the phone's installed Camera app without that
feature. If QR scanning is unavailable, type the laptop address and pairing code instead. The app
still requires Android 10 or newer. Install version 0.1.1 (versionCode 2) over the existing app to keep
its pairing and pending uploads; do not uninstall or clear app data merely to update it.

## Release signing

What exists today is the **debug APK** (`app\build\outputs\apk\debug\app-debug.apk`, about 40 MB). It is
signed with Android's standard debug key, installs on any phone with USB debugging, and is what the
project is demonstrated with. **An unsigned or debug APK is perfectly fine for the demonstration** — read
this section only if the app is ever to be handed to a store outside the project, or put on Play.

A release build is signed with a keystore **you** create and keep; this project deliberately contains no
keystore and no password.

1. Create your own keystore with `keytool` (it ships with the JDK). Choose the password yourself when it
   asks, and keep the file **outside** this project folder, for example in your user folder:

   ```powershell
   & "$env:JAVA_HOME\bin\keytool.exe" -genkeypair -v `
       -keystore "$env:USERPROFILE\invoice-capture-release.jks" `
       -alias invoice-capture -keyalg RSA -keysize 4096 -validity 10000
   ```

   Keep the file and the password safe: the same key must sign every later update of the app. Never put
   either in the project, in the README, or anywhere they would be shared.

2. Tell Gradle where they are, without writing them into a build file. Either add them to
   `local.properties` (which stays on this laptop and is not part of the source), or to
   `%USERPROFILE%\.gradle\gradle.properties`, or pass them as environment variables in the shell that
   runs the build:

   ```properties
   # local.properties or %USERPROFILE%\.gradle\gradle.properties — never shared
   RELEASE_STORE_FILE=C:\\Users\\<you>\\invoice-capture-release.jks
   RELEASE_STORE_PASSWORD=<the password you chose>
   RELEASE_KEY_ALIAS=invoice-capture
   RELEASE_KEY_PASSWORD=<the password you chose>
   ```

3. Add the signing config to `app\build.gradle.kts`. It reads the values above; no credential is written
   in the file itself, and the build still works without them (it just produces an unsigned release):

   ```kotlin
   // app/build.gradle.kts — placeholders only; the values come from properties or the environment.
   fun secret(name: String): String? =
       (project.findProperty(name) as String?)
           ?: java.util.Properties().apply {
               rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
           }.getProperty(name)
           ?: System.getenv(name)

   android {
       signingConfigs {
           create("release") {
               val store = secret("RELEASE_STORE_FILE")
               if (store != null) {
                   storeFile = file(store)
                   storePassword = secret("RELEASE_STORE_PASSWORD")
                   keyAlias = secret("RELEASE_KEY_ALIAS")
                   keyPassword = secret("RELEASE_KEY_PASSWORD")
               }
           }
       }

       buildTypes {
           release {
               signingConfig = signingConfigs.getByName("release")
               isMinifyEnabled = true
               isShrinkResources = true
               proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
           }
       }
   }
   ```

4. Build it:

   ```powershell
   $env:JAVA_HOME = 'C:\Program Files\Zulu\zulu-21'
   .\gradlew.bat assembleRelease     # app\build\outputs\apk\release\app-release.apk
   ```

A release APK is **smaller than the ~40 MB debug build** — debug carries the debug symbols and the
no-shrinking, all-architectures layout, while a release drops them and (with `isMinifyEnabled` on) strips
unused code and resources. Expect it to land well under half the debug size. If shrinking is ever turned
on, run the release APK on a phone before handing it over: Compose, kotlinx.serialization and ML Kit all
need their ProGuard rules to survive.

## Pair with the laptop

1. Start the laptop server and put the phone on the same office Wi-Fi.
2. On the laptop, open the portal's **Pair** page (`http://<laptop-ip>:5000/pair`). It shows a QR code
   and the same values as text; the code works once, for 10 minutes.
3. On the phone, give the phone a name (for example "Galaxy A06 (store 1)") and tap **Scan the code on the
   laptop**, or type the laptop address (for example `192.168.1.20:5000`) and the code, then **Pair**.
4. The home screen opens. **Settings → Check connection** asks the laptop for `GET /api/v1/ping`.

If the laptop's address changes, update it in **Settings** (the phone stays paired). To pair again, unpair
in Settings and use a new code; revoke old phones on the portal's **Devices** page.

## Known gaps

- The user reported that the document scanner could not start on a Samsung Note20 with Android 13
  because the feature was unavailable in its Google Play services version. Version 0.1.1 adds a camera
  fallback. Successful physical camera capture, rotation/cancellation, multi-page and retake uploads
  still need checking on that phone; local verification compiles the UI and unit-tests callback routing.
- The contract has no supplier list, so **Edit** asks for the supplier key the laptop knows (for example
  `vassco`); everywhere else the supplier is shown by its Pilot name (`supplier_name`, "Vassco").
- Quality thresholds are the contract's starting values and still need calibrating on real photos.
- **Remove this line** is offered on every existing line; the laptop decides whether it may be removed.
- The app is installed as a debug build; no keystore exists in this project and no code shrinking is
  turned on. **Release signing** below explains what a release build would need.
- Logins (owner / admin / manager) are future work; the phone is identified only by its device token.

## Security note

- The app talks to one server: the paired store laptop on the office Wi-Fi, over **plain HTTP**. Cleartext
  is allowed on purpose for this project only (see `res/xml/network_security_config.xml`), because the
  laptop's local IP address comes from the QR code and cannot be fixed at build time. The cloud release
  must use HTTPS only, with proper logins instead of QR-paired device tokens.
- The device token is kept in the app's private DataStore. App backup and device-to-device transfer are
  turned off, so the token, invoice photos and prices are not copied off the phone. The token is added only
  to requests for the paired laptop's scheme, host and port (including image loading).
- Invoice photos wait for upload in app-private storage (not the shared gallery or cache) and are deleted
  as soon as the laptop's receipt matches. The app needs no camera or storage permission: capture uses
  the Google scanner or the phone's camera app. Camera mode grants access to its temporary file using
  a non-exported FileProvider restricted to the camera cache folder. Photos are copied into the pending
  queue before upload; temporary captures are removed after that copy or when capture is cancelled.
- Anyone on the office Wi-Fi could read cleartext traffic; keep that network for store devices only, and
  revoke a lost phone on the portal's **Devices** page.
