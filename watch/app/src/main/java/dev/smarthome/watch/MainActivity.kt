package dev.smarthome.watch

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Switch
import androidx.wear.compose.material.Text
import androidx.wear.compose.material.ToggleChip
import kotlinx.coroutines.launch

private const val PREFERENCES = "session"
private const val REFRESH_TOKEN = "refresh_token"

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { App(getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)) } }
    }
}

@Composable
private fun App(preferences: android.content.SharedPreferences) {
    var session by remember { mutableStateOf<Session?>(null) }
    var restoring by remember { mutableStateOf(true) }

    // On start, try to sign in again silently with the stored refresh token.
    LaunchedEffect(Unit) {
        val stored = preferences.getString(REFRESH_TOKEN, null)
        if (stored != null) session = runCatching { Backend.refresh(stored) }.getOrNull()
        restoring = false
    }

    val current = session
    when {
        restoring -> Message("Loading…")
        current == null -> SignIn { signedIn ->
            // Known gap: the refresh token is stored unencrypted in app-private storage.
            preferences.edit().putString(REFRESH_TOKEN, signedIn.refreshToken).apply()
            session = signedIn
        }
        else -> Rooms(current) {
            preferences.edit().remove(REFRESH_TOKEN).apply()
            session = null
        }
    }
}

@Composable
private fun Message(text: String) {
    ScalingLazyColumn(modifier = Modifier.fillMaxWidth()) { item { Text(text) } }
}

@Composable
private fun SignIn(onSignedIn: (Session) -> Unit) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    ScalingLazyColumn(modifier = Modifier.fillMaxWidth()) {
        item { Text("Sign in") }
        item { Field(email, "Email", KeyboardType.Email, false) { email = it } }
        item { Field(password, "Password", KeyboardType.Password, true) { password = it } }
        item {
            Chip(
                label = { Text(if (busy) "Signing in…" else "Sign in") },
                enabled = !busy && email.isNotBlank() && password.isNotEmpty(),
                colors = ChipDefaults.primaryChipColors(),
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    busy = true
                    error = null
                    scope.launch {
                        runCatching { Backend.signIn(email.trim(), password) }
                            .onSuccess(onSignedIn)
                            .onFailure { error = it.message ?: "Sign-in failed" }
                        busy = false
                    }
                },
            )
        }
        error?.let { message -> item { Text(message, color = MaterialTheme.colors.error) } }
    }
}

@Composable
private fun Field(
    value: String,
    hint: String,
    keyboard: KeyboardType,
    secret: Boolean,
    onChange: (String) -> Unit,
) {
    BasicTextField(
        value = value,
        onValueChange = onChange,
        singleLine = true,
        textStyle = TextStyle(color = Color.White),
        cursorBrush = SolidColor(Color.White),
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        visualTransformation =
            if (secret) PasswordVisualTransformation() else VisualTransformation.None,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
        decorationBox = { inner ->
            if (value.isEmpty()) Text(hint, color = Color.Gray)
            inner()
        },
    )
}

@Composable
private fun Rooms(session: Session, onSignOut: () -> Unit) {
    var rooms by remember { mutableStateOf<List<Room>>(emptyList()) }
    var status by remember { mutableStateOf<String?>("Loading…") }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        runCatching { Backend.rooms(session) }
            .onSuccess { rooms = it; status = if (it.isEmpty()) "No rooms yet" else null }
            .onFailure { status = it.message ?: "Could not load" }
    }

    fun switch(room: Room, device: String, on: Boolean) {
        scope.launch {
            runCatching { Backend.setDevice(session, room.name, device, on) }
                .onSuccess { updated -> rooms = rooms.map { if (it.name == updated.name) updated else it } }
                .onFailure { status = it.message ?: "Could not switch" }
        }
    }

    LaunchedEffect(session) { load() }

    ScalingLazyColumn(modifier = Modifier.fillMaxWidth()) {
        status?.let { message -> item { Text(message) } }
        rooms.forEach { room ->
            item { Text(room.name, style = MaterialTheme.typography.title3) }
            item { Text(summary(room)) }
            item {
                DeviceToggle("Light", room.lightOn) { switch(room, "light", it) }
            }
            item {
                DeviceToggle("Fan", room.fanOn) { switch(room, "fan", it) }
            }
        }
        item {
            Chip(
                label = { Text("Refresh") },
                colors = ChipDefaults.secondaryChipColors(),
                modifier = Modifier.fillMaxWidth(),
                onClick = { scope.launch { load() } },
            )
        }
        item {
            Chip(
                label = { Text("Sign out") },
                colors = ChipDefaults.secondaryChipColors(),
                modifier = Modifier.fillMaxWidth(),
                onClick = onSignOut,
            )
        }
    }
}

@Composable
private fun DeviceToggle(label: String, on: Boolean, onChange: (Boolean) -> Unit) {
    ToggleChip(
        checked = on,
        onCheckedChange = onChange,
        label = { Text(label) },
        toggleControl = { Switch(checked = on) },
        modifier = Modifier.fillMaxWidth(),
    )
}

private fun summary(room: Room): String {
    if (room.stale) return "Sensor offline"
    val parts = buildList {
        room.temperature?.let { add("%.1f °C".format(it)) }
        room.humidity?.let { add("%.0f %%".format(it)) }
        room.doorOpen?.let { add(if (it) "door open" else "door closed") }
    }
    return if (parts.isEmpty()) "No readings" else parts.joinToString(" · ")
}
