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

/**
 * 默认要用的 MAC：电脑有线网卡那一块。
 *
 * 校园网认证不是"这个账号的密码对了就放行"，而是"这块网卡 + 这个账号"的绑定关系。
 * 服务器只认当初登记的那块有线网卡，手机自己的 Wi-Fi MAC 递上去必然被拒。
 *
 * 这个值**不入版本库**：构建时从 `local.properties` 的 `defaultMac` 读进来
 * （见 `app/build.gradle.kts`）。clone 下来的人拿到的是空串，在界面上填自己那块网卡即可。
 */
private val DEFAULT_MAC: String = BuildConfig.DEFAULT_MAC

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
    var mac by remember { mutableStateOf(DEFAULT_MAC) }
    var status by remember { mutableStateOf("未运行") }
    var log by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
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
        OutlinedTextField(
            value = mac,
            onValueChange = { mac = it },
            label = { Text("网卡 MAC（认证只认这块）") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
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
