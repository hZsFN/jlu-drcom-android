package com.jlu.drcom

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

/** 开机后拉起前台服务（替代 Windows 上的计划任务自启）。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action == Intent.ACTION_BOOT_COMPLETED || action == "android.intent.action.QUICKBOOT_POWERON") {
            val i = Intent(context, AuthForegroundService::class.java)
                .setAction(AuthForegroundService.ACTION_START)
            ContextCompat.startForegroundService(context, i)
        }
    }
}
