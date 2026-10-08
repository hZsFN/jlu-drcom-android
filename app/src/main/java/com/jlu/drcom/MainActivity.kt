package com.jlu.drcom

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestIgnoreBatteryOptimizations()
        setContent {
            MaterialTheme { DrComScreen(this) }
        }
    }

    /** 申请电池优化白名单，否则国产 ROM 十几分钟就把前台服务掐了。 */
    private fun requestIgnoreBatteryOptimizations() {
        try {
            val pm = getSystemService(PowerManager::class.java)
            if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                startActivity(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                        .setData(Uri.parse("package:$packageName"))
                )
            }
        } catch (_: Exception) {
            // 厂商 ROM 上这个 Intent 可能不存在，忽略即可
        }
    }
}

@Composable
private fun DrComScreen(activity: ComponentActivity) {
    var account by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }
    var mac by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("未运行") }
    var log by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        // 启动时把上次保存的账号 / 密码 / MAC 读回来。
        // 密码本来就是加密落盘的（drcom.secrets_store），原先只是没人往回读，
        // 于是每次开 App 都得重输一遍——用户感受到的就是「没法保存账号密码」。
        if (account.isBlank() && password.isBlank()) {
            val saved = PythonBridge.savedCredentials(activity)
            if (saved.account.isNotBlank()) account = saved.account
            if (saved.password.isNotBlank()) password = saved.password
            if (saved.mac.isNotBlank()) mac = saved.mac
        }

        // MAC 交给程序自己读：认证报文里用本机 Wi-Fi 网卡那块最稳妥，也不该让用户操心。
        // 读不到就留空，日志里会体现出来。
        if (mac.isBlank()) {
            mac = LocalMac.read(activity)
        }

        while (true) {
            status = DrComRuntime.status
            log = DrComRuntime.log
            delay(2000)
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("JLU DrCOM", style = MaterialTheme.typography.headlineSmall)
        Text("状态：$status")

        OutlinedTextField(
            value = account,
            onValueChange = { account = it },
            label = { Text("账号") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("密码") },
            singleLine = true,
            visualTransformation = if (showPassword) {
                VisualTransformation.None
            } else {
                PasswordVisualTransformation()
            },
            trailingIcon = {
                TextButton(onClick = { showPassword = !showPassword }) {
                    Text(if (showPassword) "隐藏" else "显示")
                }
            },
            modifier = Modifier.fillMaxWidth()
        )
        Text(
            text = "网卡 MAC：" + mac.ifBlank { "（未能自动读取）" },
            style = MaterialTheme.typography.bodySmall
        )

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = {
                val i = Intent(activity, AuthForegroundService::class.java)
                    .setAction(AuthForegroundService.ACTION_START)
                    .putExtra(AuthForegroundService.EXTRA_ACCOUNT, account)
                    .putExtra(AuthForegroundService.EXTRA_PASSWORD, password)
                    .putExtra(AuthForegroundService.EXTRA_MAC, mac)
                ContextCompat.startForegroundService(activity, i)
            }) { Text("登录") }

            Button(onClick = {
                val i = Intent(activity, AuthForegroundService::class.java)
                    .setAction(AuthForegroundService.ACTION_STOP)
                activity.startService(i)
            }) { Text("退出") }
        }

        Text("日志", style = MaterialTheme.typography.titleMedium)
        Text(
            text = log.ifBlank { "（暂无）" },
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier
                .fillMaxWidth()
                .height(180.dp)
                .verticalScroll(rememberScrollState())
        )
    }
}
