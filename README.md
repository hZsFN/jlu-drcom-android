# JLU DrCOM Android

The **Android port** of a Jilin University campus-network (Dr.COM) authentication client.

The desktop edition is [JLU DrCOM NG](https://github.com/hZsFN/jlu-drcom-ng) (Python + Flet, Windows/macOS). This one puts the *same authentication core* on a phone: **not a single line of the Python core changed** — only the platform layer underneath it was swapped out.

> 中文文档：[README_CN.md](README_CN.md)

---

## Why this exists

Campus authentication binds to a **network card**, not to an account. So the same dorm room, the same student ID: you authenticate fine on your laptop, then pick up your phone, join the Wi-Fi, and nothing works — because the server recognises the MAC of your laptop's **wired** NIC, and has never heard of the one your phone presents.

Hence the trade-off this app makes: put your **laptop's NIC MAC** into the phone, and let the phone "impersonate" the laptop for authentication.

State the cost plainly: **one MAC can be online in exactly one place**. If your laptop is connected and you log in from the phone, the switch sees that MAC flapping between two ports and may kick both. The correct usage is **the phone stands in while the laptop is offline**.

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
3. Fill in three fields:
   - **Account**: your student ID
   - **Password**
   - **NIC MAC**: the MAC of your laptop's **wired** adapter (`getmac /v` on Windows, or the adapter properties)
4. Tap "登录 / Log in"; the status line reports which step it reached
5. When you need the laptop online, tap "退出 / Log out" on the phone first

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
defaultMac=AA-BB-CC-DD-EE-FF      # pre-filled MAC in the UI; empty if absent
```

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

- **One MAC, one place online** (see above). That is the authentication scheme, not a bug
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
