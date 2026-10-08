package com.jlu.drcom

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Log

/**
 * 把整个进程绑到 Wi-Fi 网络上。
 *
 * 为什么需要这一手：校园网 Wi-Fi 在认证之前是「连着、但上不了网」的状态，Android 的
 * 连通性检测会判定它没有互联网，于是把**默认网络**继续留在移动数据上。Python 那边的
 * UDP socket 绑的是 `0.0.0.0`（也就是跟着默认网络走），挑战包就从蜂窝发出去了——
 * 那当然永远到不了校园网内网的认证服务器，真机上表现成「获取挑战码失败」整整十秒超时
 * （2026-10-08 手机截图）。
 *
 * `ConnectivityManager.bindProcessToNetwork()` 是进程级的（`Network.bindProcess()` 是系统级
 * 隐藏 API，第三方 app 用不了，编译期就报 Unresolved reference），而 Chaquopy 跑的 Python 跟
 * Kotlin 在同一个进程里，所以这一句就能把认证流量按回 Wi-Fi，不用去改 Python 的绑定逻辑。
 *
 * 找不到 Wi-Fi 网络时什么都不做，认证逻辑保持原样——不能因为绑不上就不让人登录。
 */
object NetworkBinder {

    private const val TAG = "DrComNet"

    @Volatile
    private var bound: Network? = null

    /**
     * 绑定到当前 Wi-Fi 网络。
     *
     * @return 给用户看的说明文字（成功或失败都返回一句），供写进认证日志。
     */
    fun bindToWifi(context: Context): String {
        return try {
            val cm = context.getSystemService(ConnectivityManager::class.java)
                ?: return "无法获取 ConnectivityManager，跳过网络绑定"

            val wifi = cm.allNetworks.firstOrNull { network ->
                cm.getNetworkCapabilities(network)
                    ?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
            } ?: return "没有可绑定的 Wi-Fi 网络（没连 Wi-Fi？），认证将走系统的默认网络"

            if (bound != wifi) {
                cm.bindProcessToNetwork(wifi)
                bound = wifi
            }
            "已把本进程绑定到 Wi-Fi 网络（避免包从移动数据出去）"
        } catch (t: Throwable) {
            Log.e(TAG, "bindToWifi failed", t)
            "绑定 Wi-Fi 网络失败：" + (t.message ?: t.javaClass.simpleName)
        }
    }
}
