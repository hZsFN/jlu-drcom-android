package com.jlu.drcom

import android.content.Context
import android.net.wifi.WifiManager
import java.io.File
import java.net.NetworkInterface

/**
 * 读本机 Wi-Fi 网卡的 MAC。
 *
 * 认证要的是「这台设备从哪块网卡出去」，填自己设备的 MAC 最不容易跟别的机器打架
 * （填了台式机的 MAC，两边同时在线就会被交换机当成 MAC 漂移）。
 *
 * 可惜 Android 6 以后普通应用基本拿不到真 MAC：`WifiInfo.getMacAddress()` 固定返回
 * `02:00:00:00:00:00`，`NetworkInterface.getHardwareAddress()` 在多数机型上给 null，
 * 新系统连 `/sys/class/net/wlan0/address` 都读不了。所以这里三条路都试，读不到就返回
 * 空串，由界面让用户手填——绝不编一个假 MAC 上去。
 */
object LocalMac {

    fun read(context: Context): String {
        fromNetworkInterface()?.let { return it }
        fromWifiManager(context)?.let { return it }
        fromSysfs()?.let { return it }
        return ""
    }

    /** 途径一：NetworkInterface（Android 7+ 多数机型返回 null）。 */
    private fun fromNetworkInterface(): String? = try {
        var found: String? = null
        val ifaces = NetworkInterface.getNetworkInterfaces()
        while (found == null && ifaces != null && ifaces.hasMoreElements()) {
            val nic = ifaces.nextElement()
            if (!nic.name.lowercase().startsWith("wlan")) continue
            val hw = nic.hardwareAddress ?: continue
            if (hw.size != 6 || hw.all { it == 0.toByte() }) continue
            found = hw.joinToString("-") { "%02X".format(it.toInt() and 0xFF) }
        }
        found
    } catch (_: Throwable) {
        null
    }

    /** 途径二：WifiInfo（新系统会返回固定的 02:00:00:00:00:00，要剔掉）。 */
    @Suppress("DEPRECATION")
    private fun fromWifiManager(context: Context): String? = try {
        val wm = context.applicationContext.getSystemService(WifiManager::class.java)
        val mac = wm?.connectionInfo?.macAddress
        if (mac.isNullOrBlank() || mac == "02:00:00:00:00:00") {
            null
        } else {
            mac.replace(':', '-').uppercase()
        }
    } catch (_: Throwable) {
        null
    }

    /** 途径三：sysfs（Android 11+ 基本被 SELinux 挡掉）。 */
    private fun fromSysfs(): String? = try {
        val f = File("/sys/class/net/wlan0/address")
        if (!f.canRead()) {
            null
        } else {
            val raw = f.readText().trim().replace(':', '-').uppercase()
            if (raw.length == 17 && raw != "02-00-00-00-00-00") raw else null
        }
    } catch (_: Throwable) {
        null
    }
}
