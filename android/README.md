# Gridiron Ticket for Android

A WebView around the live app. On open it loads the URL in `data/v2/app.json`
(https://gridironticket.grok.me/), so web, iPhone, and Android stay on one
version — no APK rebuild for a page or board change. Offline it shows a
“need a connection” splash, never a second copy of the product.

## Getting it on the phone

Open this on the phone and tap the file:

**https://predatuh.github.io/EdgeBot/get.html**

Android will ask whether to allow installs from your browser the first time; say
yes. Updates install over the top — every build is signed with the same key, so
you never have to uninstall.

## What updates on its own

The **page and the board both do.** On open the app reads `data/v2/app.json` and
loads https://gridironticket.grok.me/. Flip that URL and every phone follows
on the next launch.

Rebuild the APK only when the native shell changes (`android/` Java, icons,
signing).

## Building it

`Actions → Android APK → Run workflow`. Locally you need an Android SDK with
platform 34, then:

```
cd android && gradle assembleRelease     # -> app/build/outputs/apk/release/
```

## Signing

`debug.keystore` is committed and is the default signer. That is deliberate:
Android refuses to install an update signed with a different key, so a key
generated fresh on every CI run would mean uninstalling the app for each update.
Its password is the Android-wide default (`android` / `androiddebugkey`) and is
not pretending to be a secret — anyone reading this repo can sign a package that
Android would accept as an update to this one, which only matters if they can
also get you to install their file.

To sign with a key only you hold, set three repository secrets and the workflow
uses them instead, ignoring the committed one:

| Secret | What |
|---|---|
| `ANDROID_KEYSTORE_B64` | `base64 -w0 your.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | its password |
| `ANDROID_KEYSTORE_ALIAS` | the key alias |

Switching keys means uninstalling the old app once.

## Why an asset loader rather than `file://`

The offline splash is served from `https://appassets.androidplatform.net/assets/`
through `WebViewAssetLoader`, not from a `file://` URL. The live app is a real
https origin, so `localStorage`, cookies, and sign-in behave as they do in a
browser.
