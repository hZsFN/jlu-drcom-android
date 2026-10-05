# JLU DrCOM Android

吉林大学校园网认证客户端的 **Android 版**。

电脑上那份是 [JLU DrCOM NG](https://github.com/hZsFN/jlu-drcom-ng)（Python + Flet，Windows/macOS 桌面）。这一份把同一套认证内核搬进了手机：**Python 内核一行未改**，换掉的只是它脚下的平台层。

> English documentation: [README.md](README.md)

---

## 为什么会有这个

校园网认证绑的是**网卡**，不是账号。也就是说，同一间宿舍、同一个学号，你在电脑上认证得好好的，拿起手机连 Wi-Fi 一样上不了网——因为服务器认的是你电脑有线网卡的那串 MAC，而手机递过去的 MAC 它不认识。

于是就有了这个取舍：把**电脑那块网卡的 MAC** 填进手机 App，让手机"冒充"电脑完成认证。

代价说清楚：**同一个 MAC 同时只能在线一处**。电脑连着的时候用手机登录，交换机眼里就是这串 MAC 在两个端口之间乱跳，两边都可能被踢。正确的用法是**电脑不在线的时候，用手机顶班**。

---

## 它做什么

功能与原版一致，只是界面换成了手机友好的单页：

- **完整认证流程** —— 挑战 → 登录 → keepalive1 → keepalive2，每 20 秒一轮；掉线自动重连，用指数退避而不是疯狂重试刷爆服务器
- **把失败原因说清楚** —— 错误码映射成人话，显示「密码错误」而不是「错误码 0x03」
- **端口被占了自己想办法** —— 客户端必须绑 UDP 61440；绑不上时按「复用地址 → 改绑具体网卡地址 → 换备选端口」逐级自愈
- **网络切换能缓过来** —— Wi-Fi / 移动数据切换后重新选路，不用手动重登
- **密码不明文落盘** —— 见下方「密码怎么存的」
- **前台服务保活** —— 常驻通知栏，可随时退出；进程被 ROM 清掉后能拉起来
- **开机自启**、日志面板（账号自动脱敏）、在线时长统计

---

## 怎么用

1. 装 APK（release 包在 [Releases](https://github.com/hZsFN/jlu-drcom-android/releases)）
2. 首次启动同意**电池优化白名单**——否则国产 ROM 十几分钟就把后台服务掐了
3. 填三样东西：
   - **账号**：学号
   - **密码**
   - **网卡 MAC**：你电脑**有线网卡**的 MAC（Windows 上跑 `getmac /v` 或看适配器属性）
4. 点「登录」，状态栏会显示到哪一步了
5. 用完电脑要上网时，先在手机点「退出」

---

## 密码怎么存的

桌面版用的是 **Windows DPAPI**：密钥由登录会话派生，换机器、换账号就解不开，程序自己也还原不了。

Android 上没有等价物——Keystore 是硬件 backed 的，但只有 Java 侧能碰，Chaquopy 绕过去要把一个两百行的模块撑成八百行；而且 Keystore 里的密钥在卸载重装、恢复出厂后一样失效，性质并不更好。

所以这里做了一档**强度如实标注**的本地方案：

    随机密钥文件（应用私有目录，0600 权限）
      → HMAC-SHA256 计数器模式生成密钥流 → 与明文异或
      → 头部 8 字节随机 nonce，尾部 16 字节 HMAC 校验

它挡的是「adb 备份 / 云同步 / 顺手拷走配置文件」；挡不住「拿到这台设备、能在上面跑程序的人」。够用，但不假装是 DPAPI。

---

## 怎么构建

需要 **JDK 17** 和 **Android SDK 34**（`ANDROID_HOME` 或 `local.properties` 里的 `sdk.dir` 指向它）。

```bash
./gradlew assembleDebug      # 出 debug 包
./gradlew assembleRelease    # 出正式包（需要 keystore.properties）
```

两个可选的本地文件，都不进版本库（`.gitignore` 已排除）：

`local.properties`
```properties
sdk.dir=/path/to/android-sdk
defaultMac=AA-BB-CC-DD-EE-FF      # 界面上预填的 MAC，不填就是空
```

> ⚠️ **发布包一律用空的 `defaultMac` 构建。**
> 这个值会经 `BuildConfig.DEFAULT_MAC` 编进 `classes.dex`，用真实 MAC 打出来的包，
> 等于把构建者那块网卡的地址一并发了出去 —— 本项目犯过一次，v2.0.0～v2.1.1 的包
> 因此全部撤下重打。真值只写在本机自用版的 `local.properties` 里，仓库和发布包留空。

`keystore.properties`
```properties
storeFile=/path/to/your.jks
storePassword=...
keyAlias=...
keyPassword=...
```

缺少 `keystore.properties` 时 release 会退回 debug 签名——照样能出包，只是不能覆盖安装正式版。

---

## 结构

```
     MainActivity / AuthForegroundService        Kotlin：界面、前台服务、开机自启
                  ↓  PythonBridge
     android_main.start() / stop() / status()    Python：这一层只做转接
                  ↓
     drcom.engine.AuthEngine                     认证状态机（与桌面版逐字相同）
        ├── drcom/protocol.py                   Dr.COM 报文（逐字相同）
        ├── drcom/md4.py                        （逐字相同）
        ├── drcom/logbus.py  drcom/stats.py     （逐字相同）
        └── netiface / binding / secrets_store / config     ← 本版重写
```

## 改造点：换掉了什么

| 模块 | 桌面版（Windows） | 本版（Android） |
|---|---|---|
| `drcom/protocol.py` | — | **原样复用** |
| `drcom/engine.py` | — | **原样复用** |
| `drcom/md4.py`、`logbus.py`、`stats.py` | — | **原样复用** |
| `drcom/netiface.py` | ctypes 调 `iphlpapi`（`GetIfTable2` / `GetAdaptersAddresses`） | 读 `/sys/class/net/` + `/proc/net/dev`，`fcntl` 的 `SIOCGIFADDR` |
| `drcom/binding.py` | `netsh` 查保留端口、`SO_EXCLUSIVEADDRUSE` | 去掉这两样，自愈阶梯缩到 4 级 |
| `drcom/secrets_store.py` | Windows DPAPI | HMAC-SHA256 流加密 + 校验 |
| `drcom/config.py` | 默认数据目录走 `%APPDATA%` | 加 Android 分支（`ANDROID_PRIVATE` → 应用私有目录） |

界面与宿主是 Kotlin：Compose 单页 UI + `AuthForegroundService`（`specialUse` 类型的前台服务）+ `BootReceiver`。Python 通过 Chaquopy 嵌入，Kotlin ↔ Python 的桥就是 `PythonBridge.kt` 里那四个方法（`start` / `stop` / `status` / `recent_log`）。

> Chaquopy **两个方向都不自动转换** Python 与 Java 的容器类型：`dict` → `java.util.Map` 不行，Java `Map` → `dict` 也不行。桥的两侧都得手动掏字段。

---

## 已知限制

- **同一 MAC 不能两处同时在线**（见开头）。这是认证机制决定的，不是 bug
- **`minSdk 26`**（Android 8.0）。更低版本没测过
- **前台服务类型用的是 `specialUse`**。Android 14 起 `dataSync` 型前台服务每 24 小时累计跑满约 6 小时就会被系统停掉，而认证要挂一整天，所以不能用它
- **只带 `arm64-v8a` 和 `x86_64`** 两套 ABI。Chaquopy 按 ABI 各带一份 CPython 运行时，多一个架构就是一份 6 MB 的 `.so`
- **不做流量劫持、不装证书、不碰代理**——它只负责把认证包发出去

---

## 数据落在哪

```
/data/data/com.jlu.drcom/files/.config/jlu-drcom-ng/
├── config.json      # 账号、MAC、服务器、各项设置
├── key.bin          # 密码加密用的本地密钥
└── logs/drcom-*.log # 运行日志（账号默认脱敏）
```

排障：

```bash
adb logcat -s python:D chaquopy:D AndroidRuntime:E
adb shell run-as com.jlu.drcom cat files/.config/jlu-drcom-ng/logs/drcom-*.log
```

---

## 相关仓库

- [hZsFN/jlu-drcom-ng](https://github.com/hZsFN/jlu-drcom-ng) —— 桌面版（Python + Flet，Windows / macOS），本项目的内核来源

---

## 许可

与原项目一致，见 [LICENSE](LICENSE)。
