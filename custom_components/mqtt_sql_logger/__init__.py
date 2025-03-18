import logging
import os
import aiomysql  # type: ignore
import paho.mqtt.client as mqtt  # type: ignore

from homeassistant.core import HomeAssistant, callback  # type: ignore
from homeassistant.helpers import config_validation as cv  # type: ignore
import voluptuous as vol  # type: ignore
from homeassistant.components import websocket_api  # type: ignore
from homeassistant.components.http import StaticPathConfig  # type: ignore
from homeassistant.const import EVENT_HOMEASSISTANT_STOP  # type: ignore
from homeassistant.components.frontend import async_register_built_in_panel  # type: ignore

DOMAIN = "mqtt_sql_logger"
_LOGGER = logging.getLogger(__name__)

# Map user-friendly version strings to paho's MQTT protocol constants
MQTT_VERSION_MAP = {
    "5": mqtt.MQTTv5,
    "3.1.1": mqtt.MQTTv311,
    "3.1": mqtt.MQTTv31,
}

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("mqtt_broker"): cv.string,
                vol.Required("mqtt_port", default=8883): cv.port,
                vol.Optional("mqtt_user", default=None): cv.string,
                vol.Optional("mqtt_password", default=None): cv.string,
                vol.Optional("mqtt_topic"): cv.string,
                vol.Required("mqtt_topics"): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional("mqtt_version", default="5"): vol.In(["5", "3.1.1", "3.1"]),
                vol.Optional("mqtt_transport", default="tcp"): vol.In(["tcp", "websockets"]),
                vol.Optional("mqtt_enable_tls", default=True): cv.boolean,
                vol.Required("db_host"): cv.string,
                vol.Required("db_user"): cv.string,
                vol.Required("db_password"): cv.string,
                vol.Required("db_name"): cv.string,
                vol.Optional("log_limit", default=100): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

async def async_setup(hass: HomeAssistant, config):
    conf = config.get(DOMAIN)
    if not conf:
        return True

    www_path = os.path.join(os.path.dirname(__file__), "www")
    os.makedirs(www_path, exist_ok=True)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(f"/local/{DOMAIN}", str(www_path), False)]
    )
    _LOGGER.info("Registered static path: /local/%s", DOMAIN)

    # Extract configuration
    mqtt_broker = conf["mqtt_broker"]
    mqtt_port = conf["mqtt_port"]
    mqtt_user = conf["mqtt_user"]
    mqtt_password = conf["mqtt_password"]
    mqtt_version = conf["mqtt_version"]
    mqtt_transport = conf["mqtt_transport"]
    mqtt_enable_tls = conf["mqtt_enable_tls"]
    db_host = conf["db_host"]
    db_user = conf["db_user"]
    db_password = conf["db_password"]
    db_name = conf["db_name"]
    log_limit = conf["log_limit"]

    # Gather list of topics
    single_topic = conf.get("mqtt_topic")
    multi_topics = conf.get("mqtt_topics", [])

    if not multi_topics and single_topic:
        multi_topics = [single_topic]
    elif not multi_topics and not single_topic:
        _LOGGER.error("No 'mqtt_topic' or 'mqtt_topics' found in config!")
        return False

    _LOGGER.debug("Subscribing to MQTT topics: %s", multi_topics)

    protocol = MQTT_VERSION_MAP.get(mqtt_version, mqtt.MQTTv5)

    hass.data.setdefault(DOMAIN, {})
    # Keep a list of subscriber callbacks so we can push new logs
    hass.data[DOMAIN]["callbacks"] = []

    # Set up DB pool
    try:
        db_pool = await aiomysql.create_pool(
            host=db_host,
            user=db_user,
            password=db_password,
            db=db_name,
            charset="utf8mb4",
            autocommit=True,
        )
        _LOGGER.info("Successfully connected to database.")
    except Exception as e:
        _LOGGER.error("Database connection failed: %s", e)
        return False
    hass.data[DOMAIN]["db_pool"] = db_pool

    # Create table if needed
    create_table_query = """
        CREATE TABLE IF NOT EXISTS mqtt_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            topic VARCHAR(255) NOT NULL,
            payload TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(create_table_query)
            _LOGGER.info("Ensured mqtt_logs table exists.")
            # Create indexes if they don't exist
            indexes = [
                "ALTER TABLE mqtt_logs ADD INDEX idx_topic (topic(191))",
                "ALTER TABLE mqtt_logs ADD INDEX idx_timestamp (timestamp)",
            ]
            for idx_query in indexes:
                try:
                    await cursor.execute(idx_query)
                except Exception:
                    pass

    # Store MQTT message into the database
    async def store_message(topic, payload):
        """Store MQTT message into DB."""
        try:
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    query = "INSERT INTO mqtt_logs (topic, payload) VALUES (%s, %s)"
                    await cursor.execute(query, (topic, payload))
        except Exception as e:
            _LOGGER.error("Failed to store message in database: %s", e)

    # Fetch most recent logs from the database with limit and optional topic filter
    async def fetch_latest_logs(limit, topic=None):
        """
        Return most recent logs, optionally filtered by topic.
        If topic is None, return logs from all topics.
        """
        try:
            async with db_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    if topic is None:
                        query = (
                            "SELECT topic, payload, timestamp "
                            "FROM mqtt_logs "
                            "ORDER BY timestamp DESC "
                            "LIMIT %s"
                        )
                        await cursor.execute(query, (limit,))
                    else:
                        query = (
                            "SELECT topic, payload, timestamp "
                            "FROM mqtt_logs "
                            "WHERE topic = %s "
                            "ORDER BY timestamp DESC "
                            "LIMIT %s"
                        )
                        await cursor.execute(query, (topic, limit))
                    return await cursor.fetchall()
        except Exception as e:
            _LOGGER.error("Database error when fetching logs: %s", e)
            return []

    # Define WebSocket API
    @websocket_api.websocket_command({
        "type": "mqtt_sql_logger/get_logs",
        vol.Optional("limit"): cv.positive_int,
        vol.Optional("topic"): cv.string,
    })
    @websocket_api.async_response
    async def handle_get_logs(hass, connection, msg):
        """Return requested logs (filtered by topic if provided)."""
        limit = msg.get("limit", log_limit)
        topic = msg.get("topic")
        logs = await fetch_latest_logs(limit, topic)
        connection.send_result(msg["id"], {"logs": logs})

    @websocket_api.websocket_command({
        "type": "mqtt_sql_logger/subscribe_logs",
        vol.Optional("topic"): cv.string,
    })
    @websocket_api.async_response
    async def handle_subscribe_logs(hass, connection, msg):
        """
        Subscribe the caller to live logs. We'll push updates
        only if the log's topic matches `msg["topic"]` (or if None, all).
        """
        subscription_id = msg["id"]
        sub_topic = msg.get("topic")

        def forward_log_to_ws(incoming_topic, payload):
            # If the subscriber specified a topic, only forward if it matches
            if sub_topic is None or sub_topic == incoming_topic:
                connection.send_event(
                    subscription_id,
                    {
                        "topic": incoming_topic,
                        "payload": payload
                    }
                )

        hass.data[DOMAIN]["callbacks"].append(forward_log_to_ws)

        @callback
        def unsub():
            if forward_log_to_ws in hass.data[DOMAIN]["callbacks"]:
                hass.data[DOMAIN]["callbacks"].remove(forward_log_to_ws)

        connection.subscriptions[subscription_id] = unsub
        connection.send_result(subscription_id)

    # Register WebSocket commands
    websocket_api.async_register_command(hass, handle_get_logs)
    websocket_api.async_register_command(hass, handle_subscribe_logs)

    # Set up MQTT client
    mqtt_client_id = f"mqtt_{mqtt_version}_{DOMAIN}"
    mqtt_client = mqtt.Client(
        client_id=mqtt_client_id,
        protocol=protocol,
        transport=mqtt_transport
    )
    hass.data[DOMAIN]["mqtt_client"] = mqtt_client

    if mqtt_user and mqtt_password:
        mqtt_client.username_pw_set(mqtt_user, mqtt_password)
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

    if mqtt_enable_tls:
        import ssl
        def set_tls(client):
            client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        await hass.async_add_executor_job(set_tls, mqtt_client)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            _LOGGER.info(
                "Connected to MQTT broker at %s:%s as %s (version=%s, transport=%s, tls=%s)",
                mqtt_broker,
                mqtt_port,
                mqtt_client_id,
                mqtt_version,
                mqtt_transport,
                mqtt_enable_tls
            )
            for t in multi_topics:
                client.subscribe(t)
                _LOGGER.info("Subscribed to MQTT topic: %s", t)
        else:
            _LOGGER.error("MQTT connection failed with code %s", reason_code)

    def on_message(client, userdata, msg):
        """Handle incoming MQTT messages."""
        payload = msg.payload.decode("utf-8", "ignore")
        topic = msg.topic
        _LOGGER.debug("Received MQTT message: Topic='%s', Payload='%s'", topic, payload)
        hass.loop.create_task(store_message(topic, payload))
        # Notify all WebSocket subscribers
        for callback_fn in hass.data[DOMAIN]["callbacks"]:
            callback_fn(topic, payload)

    def on_log(client, userdata, level, buf):
        if level == mqtt.MQTT_LOG_DEBUG:
            _LOGGER.debug(buf)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_log = on_log

    # Connect to MQTT broker
    try:
        mqtt_client.connect(mqtt_broker, mqtt_port, keepalive=60)
        mqtt_client.loop_start()
    except Exception as e:
        _LOGGER.error("MQTT connection error: %s", e)
        return False

    # Clean up the MQTT client and database pool on unload or HA shutdown
    async def shutdown(event):
        """Shutdown MQTT client, stop loop, and close database pool."""
        _LOGGER.info("Shutting down MQTT client and database pool...")

        if "mqtt_client" in hass.data[DOMAIN]:
            client = hass.data[DOMAIN]["mqtt_client"]
            client.loop_stop()
            client.disconnect()

        if "db_pool" in hass.data[DOMAIN]:
            pool = hass.data[DOMAIN]["db_pool"]
            pool.close()
            await pool.wait_closed()

        _LOGGER.info("Cleanup complete for %s", DOMAIN)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    hass.data[DOMAIN]["shutdown"] = shutdown

    # REGISTER THE RELOAD SERVICE
    async def handle_reload_service(call):
        """Reload the MQTT SQL Logger integration."""
        _LOGGER.info("Reload service called for %s", DOMAIN)
        # 1) Unload current instance
        await async_unload_integration(hass)

        # 2) Re-setup with the same config
        await async_setup(hass, config)

    hass.services.async_register(
        DOMAIN, "reload", handle_reload_service,
        schema=vol.Schema({})  # no extra data needed
    )

    return True


async def async_unload_integration(hass: HomeAssistant):
    """Unload the integration."""
    if DOMAIN not in hass.data:
        return

    shutdown = hass.data[DOMAIN].pop("shutdown", None)
    if shutdown:
        await shutdown(None)
    hass.data.pop(DOMAIN, None)
    return True

async def async_unload_entry(hass: HomeAssistant):
    """Unload the integration if user removes it from UI flow."""
    await async_unload_integration(hass, None)
    return True
