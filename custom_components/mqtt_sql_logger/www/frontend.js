import {
    LitElement,
    html,
    css,
} from "https://esm.run/lit-element@4.1.1";
import {
    format
} from "https://esm.run/date-fns@4.1.0";

class MqttSqlLogger extends LitElement {
    static get properties() {
        return {
            hass: { type: Object },
            narrow: { type: Boolean },
            route: { type: Object },
            panel: { type: Object },
            _logs: { type: Array },
            _logLimit: { type: Number },
            _subscribed: { type: Boolean },
            _selectedTopic: { type: String }, // which topic is selected in UI
            _stickToBottom: { type: Boolean },
        };
    }

    constructor() {
        super();
        this._logs = [];
        this._logLimit = 100;
        this._subscribed = false;
        this._selectedTopic = ""; // "" will represent "all topics"
        this._stickToBottom = true;
    }

    render() {
        const topics = this.panel?.config?.topics || [];

        return html`
      <div class="mqtt-sql-logger-container">
        <h2>MQTT SQL Logger</h2>
  
        <!-- Topic selection dropdown -->
        <label for="topicSelect">Topic:</label>
        <select id="topicSelect" @change=${this._onTopicChange}>
          <option value="" ?selected=${this._selectedTopic === ""}>
            All topics
          </option>
          ${topics.map(
            (t) => html`
              <option value=${t} ?selected=${this._selectedTopic === t}>
                ${t}
              </option>
            `
        )}
        </select>
  
        <p>
          <label for="log-limit">Log Limit:</label>
          <input
            id="log-limit"
            type="number"
            .value=${this._logLimit}
            min="1"
            style="width:80px;margin-right:10px;"
            @change=${(e) => (this._logLimit = parseInt(e.target.value) || 100)}
          />
          <button @click=${this._fetchLogs}>Fetch Logs</button>
          <button @click=${this._clearLogs}>Clear Logs</button>
        </p>
  
        <div
          id="log-container"
          @scroll=${this._onLogScroll}
        >
          ${this._logs.map(
            (log) => (log.topic === this._selectedTopic || this._selectedTopic === "" ? html`
              <div class="line">[${log.timestamp}] ${log.topic}: ${log.payload}</div>
            ` : undefined)
        ).filter(Boolean)}
        </div>
      </div>
    `;
    }

    static get styles() {
        return css`
      :host {
        display: block;
        padding: 16px;
        background-color: var(--primary-background-color);
        color: var(--primary-text-color);
      }
      .mqtt-sql-logger-container {
        background-color: var(--card-background-color);
        color: var(--primary-text-color);
        padding: 16px;
        display: block;
        font-size: 18px;
        max-width: 95%;
        margin: 0 auto;
      }
      #log-container {
        max-height: 60vh;
        overflow-y: auto;
        border: 1px solid var(--divider-color);
        padding: 10px;
        margin-top: 1em;
      }
      #log-container div.line:nth-child(even) {
        background-color: var(--secondary-background-color);
      }
    `;
    }

    updated(changedProps) {
        // If we just got our first non-null hass
        if (changedProps.has("hass") && !changedProps.get("hass") && this.hass) {
            this._fetchLogs();
            this._subscribeUpdates(); // Subscribe to real-time logs for the default selectedTopic
        }
    }

    /**
     * Called when the user changes the dropdown for a different topic.
     * We fetch new logs & re-subscribe with the chosen topic.
     */
    _onTopicChange(e) {
        this._selectedTopic = e.target.value; // "" or a specific topic
        this._fetchLogs();
        this._subscribeUpdates(/* force */ true);
    }

    async _fetchLogs() {
        if (!this.hass) return;
        try {
            const response = await this.hass.callWS({
                type: "mqtt_sql_logger/get_logs",
                limit: this._logLimit,
                topic: undefined, // this._selectedTopic || undefined,
            });
            // By default, DB returns newest first; reversing so newest is at bottom
            this._logs = response.logs.reverse();

            // After loading, scroll if pinned
            try {
                const response = await this.hass.callWS({
                    type: "mqtt_sql_logger/get_logs",
                    limit: this._logLimit,
                });
            } catch (err) {
                console.error("Error fetching logs:", err);
            }

            // Scroll to bottom
            if (this._stickToBottom) {
                const c = this.shadowRoot.getElementById("log-container");
                if (c) c.scrollTop = c.scrollHeight;
            }

        } catch (err) {
            console.error("Error fetching logs:", err);
        }
    }

    _clearLogs() {
        this._logs = [];
        this.requestUpdate();
    }

    /**
     * Subscribe to real-time logs for the currently selected topic.
     * If force=true, we re-subscribe (i.e., even if we're already subscribed).
     */
    _subscribeUpdates(force = false) {
        if (!this.hass) return;

        if (this._subscribed && !force) {
            return; // already subscribed
        }

        // Just mark unsubscribed so we create a fresh subscription
        this._subscribed = false;

        this.hass.connection.subscribeMessage(
            (msg) => {
                // The integration sends a plain object: { topic, payload }
                const { topic, payload } = msg;
                if (!topic || payload == null) return;

                if (this._selectedTopic !== "" && this._selectedTopic !== topic) {
                    return; // not for the selected topic
                }

                // Insert the new log
                this._logs.push({
                    topic,
                    payload,
                    timestamp: format(new Date(), "yyyy-MM-ddHH:mm:ss"),
                });

                if (this._logs.length > this._logLimit) {
                    this._logs.shift();
                }

                // Re-render & scroll if pinned
                this.requestUpdate().then(() => {
                    if (this._stickToBottom) {
                        const c = this.shadowRoot.getElementById("log-container");
                        if (c) c.scrollTop = c.scrollHeight;
                    }
                });
            },
            {
                type: "mqtt_sql_logger/subscribe_logs",
                // Only get events for the selected topic (if ""), no topic => all
                topic: undefined, //this._selectedTopic || undefined,
            }
        );
        this._subscribed = true;
    }

    /**
     * Track whether the user has scrolled away from the bottom.
     */
    _onLogScroll(e) {
        const el = e.target;
        const threshold = 10;
        const atBottom =
            el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
        this._stickToBottom = atBottom;
    }
}

customElements.define("mqtt-sql-logger", MqttSqlLogger);