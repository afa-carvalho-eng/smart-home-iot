package dev.smarthome.watch

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class Room(
    val name: String,
    val temperature: Double?,
    val humidity: Double?,
    val doorOpen: Boolean?,
    val lightOn: Boolean,
    val fanOn: Boolean,
    val stale: Boolean,
)

data class Session(val idToken: String, val refreshToken: String?)

class BackendError(message: String) : Exception(message)

/** Calls Amazon Cognito (sign-in) and the smart home HTTP API. */
object Backend {
    private val cognitoUrl = "https://cognito-idp.${BuildConfig.COGNITO_REGION}.amazonaws.com/"

    suspend fun signIn(email: String, password: String): Session =
        authenticate(
            "USER_PASSWORD_AUTH",
            JSONObject().put("USERNAME", email).put("PASSWORD", password),
        )

    /** Exchanges the stored refresh token for a new ID token, without the password. */
    suspend fun refresh(refreshToken: String): Session =
        authenticate("REFRESH_TOKEN_AUTH", JSONObject().put("REFRESH_TOKEN", refreshToken))
            .copy(refreshToken = refreshToken)

    private suspend fun authenticate(flow: String, parameters: JSONObject): Session {
        val body = JSONObject()
            .put("AuthFlow", flow)
            .put("ClientId", BuildConfig.COGNITO_CLIENT_ID)
            .put("AuthParameters", parameters)
        val headers = mapOf(
            "Content-Type" to "application/x-amz-json-1.1",
            "X-Amz-Target" to "AWSCognitoIdentityProviderService.InitiateAuth",
        )
        val result = request("POST", cognitoUrl, headers, body)
            .optJSONObject("AuthenticationResult")
            ?: throw BackendError("Sign-in needs a step this app does not support")
        val refreshToken = result.optString("RefreshToken").ifEmpty { null }
        return Session(result.getString("IdToken"), refreshToken)
    }

    suspend fun rooms(session: Session): List<Room> {
        val rooms = request("GET", "${BuildConfig.API_URL}/rooms", bearer(session), null)
            .getJSONArray("rooms")
        return List(rooms.length()) { index -> parseRoom(rooms.getJSONObject(index)) }
    }

    suspend fun setDevice(session: Session, room: String, device: String, on: Boolean): Room {
        val url = "${BuildConfig.API_URL}/rooms/$room/devices/$device"
        return parseRoom(request("POST", url, bearer(session), JSONObject().put("on", on)))
    }

    private fun bearer(session: Session) =
        mapOf("Authorization" to "Bearer ${session.idToken}", "Content-Type" to "application/json")

    private fun parseRoom(json: JSONObject): Room {
        val sensors = json.getJSONObject("sensors")
        val devices = json.getJSONObject("devices")
        return Room(
            name = json.getString("room"),
            temperature = if (sensors.has("temperature")) sensors.getDouble("temperature") else null,
            humidity = if (sensors.has("humidity")) sensors.getDouble("humidity") else null,
            doorOpen = if (sensors.has("door_open")) sensors.getBoolean("door_open") else null,
            lightOn = devices.optBoolean("light"),
            fanOn = devices.optBoolean("fan"),
            stale = json.optBoolean("stale"),
        )
    }

    private suspend fun request(
        method: String,
        url: String,
        headers: Map<String, String>,
        body: JSONObject?,
    ): JSONObject = withContext(Dispatchers.IO) {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 10_000
            connection.readTimeout = 10_000
            headers.forEach { (name, value) -> connection.setRequestProperty(name, value) }
            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (status !in 200..299) {
                // Cognito puts its reason in "message"; the API uses "error".
                val json = runCatching { JSONObject(text) }.getOrNull()
                val reason = json?.optString("message")?.ifEmpty { json.optString("error") }
                throw BackendError(reason?.ifEmpty { null } ?: "Request failed ($status)")
            }
            JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }
}
