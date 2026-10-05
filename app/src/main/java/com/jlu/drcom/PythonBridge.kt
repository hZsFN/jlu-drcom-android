package com.jlu.drcom

import android.content.Context
import android.util.Log
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Kotlin 与 Python 的唯一边界。UI 和 Service 都不直接碰 Chaquopy。
 *
 * 约定：src/main/python/android_main.py 暴露 start(config) / stop() / status() / recent_log(n)。
 */
object PythonBridge {

    private const val TAG = "DrComPy"

    @Volatile
    private var booted = false

    @Synchronized
    fun boot(context: Context) {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
        booted = true
    }

    private fun module(context: Context): PyObject {
        boot(context)
        return Python.getInstance().getModule("android_main")
    }

    fun start(context: Context, account: String, password: String, mac: String): Boolean {
        return try {
            val cfg = mapOf("account" to account, "password" to password, "mac" to mac)
            module(context).callAttr("start", cfg).toJava(Boolean::class.java) ?: false
        } catch (t: Throwable) {
            Log.e(TAG, "start failed", t)
            false
        }
    }

    fun stop(context: Context): Boolean {
        return try {
            module(context).callAttr("stop").toJava(Boolean::class.java) ?: false
        } catch (t: Throwable) {
            Log.e(TAG, "stop failed", t)
            false
        }
    }

    /** 界面只关心这几个字段。 */
    private val STATUS_TEXT_KEYS =
        listOf("status", "detail", "account", "ip", "error", "engine_state")

    /**
     * 不整包要一个 Map —— Chaquopy 不肯把 Python 的 dict 直接转成 java.util.Map，
     * 会抛 `TypeError: Cannot convert dict object to java.util.Map`。
     * 逐字段取是稳的：字符串照搬，缺失或 None 都得到 null。
     */
    fun status(context: Context): Map<String, Any?> {
        return try {
            val py = module(context).callAttr("status")
            val out = LinkedHashMap<String, Any?>()
            for (key in STATUS_TEXT_KEYS) {
                out[key] = py.callAttr("get", key)?.toJava(String::class.java)
            }
            out["online"] = py.callAttr("get", "online")?.toJava(Boolean::class.java) ?: false
            out
        } catch (t: Throwable) {
            Log.e(TAG, "status failed", t)
            mapOf("status" to "异常：" + (t.message ?: ""))
        }
    }

    fun recentLog(context: Context, n: Int = 40): String {
        return try {
            module(context).callAttr("recent_log", n).toString()
        } catch (t: Throwable) {
            Log.e(TAG, "recent_log failed", t)
            ""
        }
    }
}

/** Service 与 UI 共享的运行期状态（进程内单例）。 */
object DrComRuntime {
    @Volatile var status: String = "未运行"
    @Volatile var log: String = ""

    fun refresh(context: Context) {
        val st = PythonBridge.status(context)
        status = st["status"]?.toString() ?: "未知"
        log = PythonBridge.recentLog(context)
    }
}
