# Gridiron Ticket for Android

A WebView around `webapp/parlay.html` — the same page the site serves, bundled
into the APK. It is a real installed app: its own icon, its own back stack, no
browser chrome, and it opens instantly because nothing is downloaded first.

## Getting it on the phone

Open this on the phone and tap the file:

**https://github.com/Predatuh/EdgeBot/releases/latest/download/gridiron-ticket.apk**

Android will ask whether to allow installs from your browser the first time; say
yes. Updates install over the top — every build is signed with the same key, so
you never have to uninstall.

## What updates on its own

The **board does**. The page fetches `data/v2/parlay.json` and
`data/v2/stats.json` from the repo every time the app opens, so the tickets are
as current as the last workflow run without reinstalling anything.

The **app shell does not**. Rebuild the APK when the page itself changes —
that is what the `Android APK` workflow does, and it runs automatically on any
push that touches `android/`, `webapp/` or `parlay.py`.

## Building it

`Actions → Android APK → Run workflow`. The runner has the Android SDK already;
the build copies `docs/` into `app/src/main/assets/`, compiles, signs, and
attaches the result to the `app` release.

Locally you need an Android SDK with platform 34, then:

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

The page is served from `https://appassets.androidplatform.net/assets/` through
`WebViewAssetLoader`, not from a `file://` URL. A `file://` origin gets
unreliable per-path `localStorage` — pins, filters and the theme would come back
empty — and a `fetch` from it to `raw.githubusercontent.com` has no usable
origin for CORS. On a real https origin both behave exactly as they do in a
browser, with no `setAllowUniversalAccessFromFileURLs` hole opened up.

The only permission the app asks for is `INTERNET`.
