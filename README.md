# MQTT SQL Logger

A Home Assistant (HACS) integration for logging MQTT messages into a MariaDB/MySQL database. This must be installed via [Custom Repositories](https://www.hacs.xyz/docs/faq/custom_repositories/) for now.

#### Useful Integrations

1) [Mosquitto](https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md) or [EMQX](https://github.com/hassio-addons/addon-emqx/blob/main/emqx/DOCS.md)
2) [MariaDB](https://github.com/home-assistant/addons/blob/master/mariadb/DOCS.md)
3) [phpMyAdmin](https://github.com/hassio-addons/addon-phpmyadmin) (optional)

#### Setup

1) Install [HACS](https://www.hacs.xyz/docs/use/#getting-started-with-hacs) into your Home Assistant instance.
2) Add this repo with [Custom Repositories](https://www.hacs.xyz/docs/faq/custom_repositories/).
   ```
   https://github.com/LouisT/mqtt_sql_logger
   ```
3) Modify and apply the following example config to [configuration.yaml](https://www.home-assistant.io/docs/configuration/).

```yaml
logger:
  default: warn
  logs:
    custom_components.mqtt_sql_logger: info
    paho.mqtt.client: info

mqtt_sql_logger:
  mqtt_broker: "core-mosquitto" # https://github.com/home-assistant/addons/blob/master/mosquitto/DOCS.md
  mqtt_port: 1883
  mqtt_topics:
    - first-topic
    - second-topic
  mqtt_user: "a-valid-user"
  mqtt_password: "a-valid-password"
  mqtt_version: "5" # 5, 3.1, 3.1.1 (default: 5)
  mqtt_transport: "tcp" # tcp, websocket (default: tcp)
  mqtt_enable_tls: false # true/false (default: false)
  db_host: "core-mariadb" # https://github.com/home-assistant/addons/blob/master/mariadb/DOCS.md
  db_user: "database-user"
  db_password: "database-password"
  db_name: "log-database"
  log_limit: 100 # The number of rows to show in the frontend (default: 100)

panel_custom:
  - name: mqtt-sql-logger
    url_path: mqtt-logger
    sidebar_title: "MQTT SQL Logger"
    sidebar_icon: mdi:database-import # https://pictogrammers.com/library/mdi/
    module_url: /local/mqtt_sql_logger/frontend.js
    config:
      log_limit: 100 # TODO: Get log_limit over WebSocket.
      topics: # TODO: Get topics over WebSocket.
        - first-topic
        - second-topic
```

### Known Issues
1) Selecting a topic only hides the messages in the UI; they're still sent via WebSocket.



