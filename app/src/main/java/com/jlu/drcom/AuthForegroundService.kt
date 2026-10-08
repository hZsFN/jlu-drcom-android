package com.jlu.drcom

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/**
 * 承载 Python 认证引擎的前台服务。
 *
 * 之所以必须是前台服务：Android 8 以后后台进程会被冻结，而校园网认证需要长时间挂着
 * 心跳、断线自动重连；只有前台服务 + 常驻通知才能长期存活。
 */
class AuthForegroundService : Service() {

    companion object {
        const val ACTION_START = "com.jlu.drcom.action.START"
        const val ACTION_STOP = "com.jlu.drcom.action.STOP"
        const val EXTRA_ACCOUNT = "account"
        const val EXTRA_PASSWORD = "password"
        const val EXTRA_MAC = "mac"
        private const val CHANNEL_ID = "drcom_auth"
        private const val NOTIFICATION_ID = 1001

        //: 与 AndroidManifest 的 foregroundServiceType 保持一致。
        //: dataSync 在 Android 14 上有「每 24 小时累计约 6 小时」上限，认证要挂一整天，扛不住。
        private val FGS_TYPE = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
    }

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notif_channel_name),
                NotificationManager.IMPORTANCE_LOW
            )
            ch.setShowBadge(false)
            nm.createNotificationChannel(ch)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val account = intent.getStringExtra(EXTRA_ACCOUNT).orEmpty()
                val password = intent.getStringExtra(EXTRA_PASSWORD).orEmpty()
                val mac = intent.getStringExtra(EXTRA_MAC).orEmpty()
                ServiceCompat.startForeground(
                    this,
                    NOTIFICATION_ID,
                    buildNotification("正在登录…"),
                    FGS_TYPE
                )
                // 必须在 Python 起来之前绑：未认证的校园网 Wi-Fi 会被系统判成「没有互联网」，
                // 默认网络留在移动数据上，挑战包就从 5G 出去了（2026-10-08 真机上的症状）。
                PythonBridge.note(this, NetworkBinder.bindToWifi(this))
                val ok = PythonBridge.start(this, account, password, mac)
                DrComRuntime.refresh(this)
                push(buildNotification(if (ok) DrComRuntime.status else "启动失败"))
            }

            ACTION_STOP -> {
                PythonBridge.stop(this)
                DrComRuntime.refresh(this)
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }

            else -> {
                ServiceCompat.startForeground(
                    this,
                    NOTIFICATION_ID,
                    buildNotification(DrComRuntime.status),
                    FGS_TYPE
                )
                DrComRuntime.refresh(this)
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val exit = PendingIntent.getService(
            this,
            1,
            Intent(this, AuthForegroundService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notif_title))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(0, "退出", exit)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun push(n: Notification) {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, n)
    }
}
