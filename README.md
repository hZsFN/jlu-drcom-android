# JLU DrCOM Android

The **Android port** of a Jilin University campus-network (Dr.COM) authentication client.

The desktop edition is [JLU DrCOM NG](https://github.com/hZsFN/jlu-drcom-ng) (Python + Flet, Windows/macOS). This one puts the *same authentication core* on a phone: **not a single line of the Python core changed** — only the platform layer underneath it was swapped out.

> 中文文档：[README_CN.md](README_CN.md)

---

## Why this exists

Campus authentication tracks your online state by **MAC**: whichever MAC the client reports in its authentication packets is the one the server lets through. So the phone can simply use **its own Wi-Fi NIC's** MAC — no impersonation required. That contradicts what this repository's earlier docs claimed; it was verified on a real device in October 2026.

The app reads the local Wi-Fi MAC itself (`LocalMac`, trying `NetworkInterface` → `WifiInfo` → `sysfs` in turn) and only displays it — you never fill it in.

> One real-device gotcha worth recording: an unauthenticated campus Wi-Fi is seen by Android as "connected but no internet", so the system keeps the **default network** on mobile data and the authentication packets leave over 5G. Before starting authentication the app calls `ConnectivityManager.bindProcessToNetwork()` to bind its process to Wi-Fi, so the traffic goes out the right NIC.

---

## What it does

Feature parity with the desktop edition; the UI is just a single phone-friendly page.

- **Full authentication flow** — challenge → login → keepalive1 → keepalive2, one round every 20 seconds; automatic reconnect with exponential backoff instead of hammering the server
- **Failures explained in plain words** — error codes mapped to human sentences: "wrong password" rather than "error code 0x03"
- **Port conflicts handled** — the client must bind UDP 61440; on failure it escalates through *reuse address → bind a concrete NIC address → alternate port*
- **Survives network changes** — re-selects the route after Wi-Fi / mobile-data switches, no manual re-login
- **Password never stored in the clear** — see "How the password is stored"
- **Foreground service keep-alive** — persistent notification, exit any time; restarts if the ROM kills the process
- **Boot autostart**, log panel (accounts masked), online-time statistics

---

## Usage

1. Install the APK (release builds are in [Releases](https://github.com/hZsFN/jlu-drcom-android/releases))
2. On first launch, accept the **battery-optimisation whitelist** — otherwise most Chinese OEM ROMs kill the background service within minutes
3. Fill in **Account** (your student ID) and **Password** — that is all. The MAC is read by the app itself and shown read-only
4. Tap "登录 / Log in"; the status line reports which step it reached
5. When you want the laptop online instead, tap "退出 / Log out" on the phone first

---

## How the password is stored

The desktop edition uses **Windows DPAPI**: the key derives from the login session, so another machine or another user account cannot decrypt it — the program itself cannot either.

Android has no equivalent. Keystore is hardware-backed, but only reachable from Java; tunnelling through it from Chaquopy would turn a 200-line module into 800. And Keystore keys are lost on uninstall or factory reset anyway, so the property would not be strictly better.

So this port does a **honestly labelled** local scheme:

    random key file (app-private dir, mode 0600)
      → HMAC-SHA256 in counter mode produces a keystream → XOR with the plaintext
      → 8-byte random nonce at the head, 16-byte HMAC tag at the tail

It defends against *adb backups / cloud sync / someone copying the config off the device*. It does **not** defend against *someone holding the device who can run code on it*. That is enough here, and it does not pretend to be DPAPI.

---

## Building

You need **JDK 17** and **Android SDK 34** (`ANDROID_HOME`, or `sdk.dir` in `local.properties`).

```bash
./gradlew assembleDebug      # debug build
./gradlew assembleRelease    # release build (needs keystore.properties)
```

Two optional local files, neither tracked by git (already in `.gitignore`):

`local.properties`
```properties
sdk.dir=/path/to/android-sdk
```

> This file used to carry a `defaultMac`, which pre-filled the PC NIC's MAC into the UI.
> Since 2.1.3 the app reads the local MAC by itself and the field is gone — which also
> removes the old hazard of "the builder's NIC address compiled into `classes.dex`"
> (that is exactly what forced the v2.0.0–v2.1.1 packages to be pulled and rebuilt).

`keystore.properties`
```properties
storeFile=/path/to/your.jks
storePassword=...
keyAlias=...
keyPassword=...
```

Without `keystore.properties`, release falls back to the debug signing key — you still get an APK, it just cannot overwrite an official install.

---

## What was replaced

| Module | Desktop (Windows) | This port (Android) |
|---|---|---|
| `drcom/protocol.py` | — | **reused as-is** |
| `drcom/engine.py` | — | **reused as-is** |
| `drcom/md4.py`, `logbus.py`, `stats.py` | — | **reused as-is** |
| `drcom/netiface.py` | ctypes into `iphlpapi` (`GetIfTable2` / `GetAdaptersAddresses`) | reads `/sys/class/net/` + `/proc/net/dev`, `SIOCGIFADDR` via `fcntl` |
| `drcom/binding.py` | `netsh` reserved-port probing, `SO_EXCLUSIVEADDRUSE` | both dropped; self-healing ladder cut to 4 steps |
| `drcom/secrets_store.py` | Windows DPAPI | HMAC-SHA256 keystream + tag |
| `drcom/config.py` | data dir under `%APPDATA%` | Android branch added (`ANDROID_PRIVATE` → app-private dir) |

The UI and host are Kotlin: a single Compose page, `AuthForegroundService` (a `specialUse` foreground service) and a `BootReceiver`. Python is embedded with Chaquopy; the whole Kotlin ↔ Python bridge is the four methods in `PythonBridge.kt` (`start` / `stop` / `status` / `recent_log`).

> Chaquopy does **not** auto-convert container types in *either* direction: `dict` → `java.util.Map` fails, and a Java `Map` → `dict` fails too. Both sides of the bridge have to pull fields out by hand.

---

## Known limitations

- **Switching devices means re-authenticating**: the session is tracked by MAC, so one account is one device at a time on the campus network
- **`minSdk 26`** (Android 8.0). Older versions are untested
- **The foreground service uses the `specialUse` type.** From Android 14 on, a `dataSync` foreground service is stopped by the system once it accumulates roughly 6 hours in 24 — and authentication has to run all day, so `dataSync` is not an option
- **Only `arm64-v8a` and `x86_64`** are packaged. Chaquopy ships a separate CPython runtime per ABI, and each one is a ~6 MB `.so`
- **No traffic interception, no certificates, no proxy** — it only sends the authentication packets

---

## Where the data lives

```
/data/data/com.jlu.drcom/files/.config/jlu-drcom-ng/
├── config.json      # accounts, MAC, server, settings
├── key.bin          # local key for password encryption
└── logs/drcom-*.log # run log (accounts masked by default)
```

Troubleshooting:

```bash
adb logcat -s python:D chaquopy:D AndroidRuntime:E
adb shell run-as com.jlu.drcom cat files/.config/jlu-drcom-ng/logs/drcom-*.log
```

---

## Related repositories

- [hZsFN/jlu-drcom-ng](https://github.com/hZsFN/jlu-drcom-ng) — the desktop edition (Python + Flet, Windows / macOS); this project's core comes from there

---

## License

Same as the original project — see [LICENSE](LICENSE).
