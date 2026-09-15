package app.gridiron.ticket;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Thin shell around the live Gridiron Ticket page. The phone loads
 * https://predatuh.github.io/EdgeBot/ (or whatever data/v2/app.json says),
 * so a site update is an app update — no APK rebuild. Bundled assets are
 * only the offline fallback.
 */
public class MainActivity extends android.app.Activity {

  private static final String ASSET_BASE = "https://appassets.androidplatform.net";
  private static final String LIVE_DEFAULT = "https://predatuh.github.io/EdgeBot/";
  private static final String APP_JSON =
      "https://raw.githubusercontent.com/Predatuh/EdgeBot/main/data/v2/app.json";
  private static final String CHANNEL = "board";
  private WebView web;
  private boolean fellBack;

  @SuppressLint("SetJavaScriptEnabled")
  @Override protected void onCreate(Bundle state) {
    super.onCreate(state);
    ensureChannel();

    final WebViewAssetLoader loader = new WebViewAssetLoader.Builder()
        .setDomain("appassets.androidplatform.net")
        .addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this))
        .build();

    web = new WebView(this);
    web.setBackgroundColor(getColor(R.color.app_bg));
    web.setOverScrollMode(View.OVER_SCROLL_NEVER);

    WebSettings s = web.getSettings();
    s.setJavaScriptEnabled(true);
    s.setDomStorageEnabled(true);
    s.setSupportZoom(false);
    s.setBuiltInZoomControls(false);
    s.setMediaPlaybackRequiresUserGesture(true);
    s.setAllowFileAccess(false);
    s.setAllowContentAccess(false);
    s.setCacheMode(WebSettings.LOAD_DEFAULT);

    CookieManager cookies = CookieManager.getInstance();
    cookies.setAcceptCookie(true);
    cookies.setAcceptThirdPartyCookies(web, true);

    web.addJavascriptInterface(new Host(), "GTHost");

    web.setWebViewClient(new WebViewClient() {
      @Override public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
        return loader.shouldInterceptRequest(r.getUrl());
      }
      @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
        Uri u = r.getUrl();
        if (isAppHost(u)) return false;
        try {
          startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (Exception ignored) { }
        return true;
      }
      @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
        if (r == null || !r.isForMainFrame() || fellBack) return;
        Uri u = r.getUrl();
        if (u != null && "appassets.androidplatform.net".equals(u.getHost())) return;
        fellBack = true;
        v.loadUrl(ASSET_BASE + "/assets/index.html");
      }
    });

    setContentView(web);
    if (state != null) web.restoreState(state);
    else boot();
  }

  private void boot() {
    new Thread(() -> {
      String target = LIVE_DEFAULT;
      HttpURLConnection c = null;
      try {
        URL u = new URL(APP_JSON);
        c = (HttpURLConnection) u.openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(4000);
        c.setRequestProperty("Accept", "application/json");
        if (c.getResponseCode() == 200) {
          InputStream in = c.getInputStream();
          ByteArrayOutputStream buf = new ByteArrayOutputStream();
          byte[] b = new byte[1024];
          int n;
          while ((n = in.read(b)) > 0) buf.write(b, 0, n);
          in.close();
          JSONObject o = new JSONObject(buf.toString("UTF-8"));
          String url = o.optString("url", "").trim();
          String ver = o.optString("version", "").trim();
          if (url.startsWith("https://")) {
            target = url;
            if (ver.length() > 0) {
              target += (target.contains("?") ? "&" : "?") + "v=" + Uri.encode(ver);
            }
          }
        }
      } catch (Exception ignored) {
      } finally {
        if (c != null) c.disconnect();
      }
      final String go = target;
      runOnUiThread(() -> web.loadUrl(go));
    }).start();
  }

  static boolean isAppHost(Uri u) {
    if (u == null) return false;
    String h = u.getHost();
    if (h == null) return false;
    h = h.toLowerCase();
    return h.equals("appassets.androidplatform.net")
        || h.equals("predatuh.github.io")
        || h.equals("grok.me")
        || h.endsWith(".grok.me");
  }

  /** Lets the page keep the system bars the same colour as itself when the
   *  in-app light/dark toggle is used, and fire a native ping when a new
   *  board lands. Nothing else is exposed. */
  private class Host {
    @JavascriptInterface public void themeColor(final String css) {
      final Integer c = parseCss(css);
      if (c == null) return;
      runOnUiThread(new Runnable() { @Override public void run() { paintBars(c); } });
    }
    @JavascriptInterface public void requestNotify() {
      if (Build.VERSION.SDK_INT >= 33) {
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) {
          requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 7);
        }
      }
    }
    @JavascriptInterface public void boardArrived(final String title, final String body) {
      runOnUiThread(new Runnable() {
        @Override public void run() { postBoard(title, body); }
      });
    }
  }

  private void ensureChannel() {
    if (Build.VERSION.SDK_INT < 26) return;
    NotificationChannel ch = new NotificationChannel(
        CHANNEL, "Morning board", NotificationManager.IMPORTANCE_DEFAULT);
    ch.setDescription("Fires when a new Gridiron Ticket board is on GitHub.");
    NotificationManager nm = getSystemService(NotificationManager.class);
    if (nm != null) nm.createNotificationChannel(ch);
  }

  private void postBoard(String title, String body) {
    if (Build.VERSION.SDK_INT >= 33
        && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
        != PackageManager.PERMISSION_GRANTED) {
      return;
    }
    Intent open = new Intent(this, MainActivity.class)
        .setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
    int flags = PendingIntent.FLAG_UPDATE_CURRENT;
    if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
    PendingIntent pi = PendingIntent.getActivity(this, 1, open, flags);
    android.app.Notification.Builder b;
    if (Build.VERSION.SDK_INT >= 26) {
      b = new android.app.Notification.Builder(this, CHANNEL);
    } else {
      b = new android.app.Notification.Builder(this);
    }
    android.app.Notification n = b
        .setSmallIcon(R.mipmap.ic_launcher)
        .setContentTitle(title == null ? "Morning board is up" : title)
        .setContentText(body == null ? "" : body)
        .setContentIntent(pi)
        .setAutoCancel(true)
        .build();
    NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
    if (nm != null) nm.notify(42, n);
  }

  /** "rgb(8, 12, 16)" / "rgba(8, 12, 16, 1)" / "#080C10" -> colour int, or null. */
  static Integer parseCss(String css) {
    if (css == null) return null;
    css = css.trim();
    try {
      if (css.startsWith("#")) return Color.parseColor(css);
      int open = css.indexOf('('), close = css.indexOf(')');
      if (open < 0 || close <= open) return null;
      String[] parts = css.substring(open + 1, close).split("[,/]");
      if (parts.length < 3) return null;
      int r = (int) Float.parseFloat(parts[0].trim());
      int g = (int) Float.parseFloat(parts[1].trim());
      int b = (int) Float.parseFloat(parts[2].trim());
      return Color.rgb(clamp(r), clamp(g), clamp(b));
    } catch (Exception e) {
      return null;
    }
  }

  private static int clamp(int v) { return v < 0 ? 0 : (v > 255 ? 255 : v); }

  private void paintBars(int colour) {
    Window w = getWindow();
    w.setStatusBarColor(colour);
    w.setNavigationBarColor(colour);
    boolean light = (0.299 * Color.red(colour) + 0.587 * Color.green(colour)
                     + 0.114 * Color.blue(colour)) > 150;
    View decor = w.getDecorView();
    int flags = decor.getSystemUiVisibility();
    if (light) flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
    else flags &= ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      if (light) flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
      else flags &= ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
    }
    decor.setSystemUiVisibility(flags);
  }

  @Override public void onBackPressed() {
    web.evaluateJavascript("(function(){try{return !!(window.__gtBack&&window.__gtBack())}"
        + "catch(e){return false}})()", value -> {
      if (!"true".equals(value)) finish();
    });
  }

  @Override protected void onSaveInstanceState(Bundle out) {
    super.onSaveInstanceState(out);
    web.saveState(out);
  }

  @Override protected void onDestroy() {
    if (web != null) web.destroy();
    super.onDestroy();
  }
}
