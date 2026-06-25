/**
 * SmartBus chat frontend.
 *
 * Talks to POST /api/chat/ — see agent/views.py for the exact contract:
 *   request:  { traveler_external_id, message }
 *   response: { reply, tool_calls_made } on success
 *             { error } on 4xx/5xx
 *
 * Seat map:
 *   When the agent's reply signals seat selection it includes a JSON block:
 *     <!--SEATS:{"trip_id":"<id>","available":[1,3,5,…],"total":44}-->
 *   The frontend strips that block, renders the reply text normally, then
 *   appends an interactive seat map widget to the message list.
 *   Clicking a seat auto-sends "I'd like to book seat <N>" into the chat.
 *
 *   If the backend doesn't yet emit the marker, the seat map simply won't
 *   appear — the rest of the chat is unaffected.
 *
 * No frameworks, no build step. State lives in plain JS variables for
 * the lifetime of the page load — nothing is persisted client-side.
 */

(() => {
    const CHAT_ENDPOINT = "/api/chat/";

    const entryScreen        = document.getElementById("entry-screen");
    const chatScreen         = document.getElementById("chat-screen");
    const phoneInput         = document.getElementById("phone-input");
    const startButton        = document.getElementById("start-button");
    const entryError         = document.getElementById("entry-error");
    const travelerIdDisplay  = document.getElementById("traveler-id-display");
    const messageList        = document.getElementById("message-list");
    const chatForm           = document.getElementById("chat-form");
    const messageInput       = document.getElementById("message-input");
    const sendButton         = document.getElementById("send-button");
    const typingIndicator    = document.getElementById("typing-indicator");

    let travelerExternalId = null;

    // ── Helpers ────────────────────────────────────────────────────────

    function normalizePhone(raw) {
        const trimmed = raw.trim();
        if (!trimmed) return null;
        const valid = /^\+?[0-9]{6,15}$/.test(trimmed);
        return valid ? trimmed : null;
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    /**
     * Render basic markdown-ish formatting from the agent's reply:
     * **bold**, and simple "| a | b |" table rows into a real <table>.
     */
    function formatReply(text) {
        let safe = escapeHtml(text);
        safe = safe.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

        const lines = safe.split("\n");
        let html = "";
        let tableLines = [];

        const flushTable = () => {
            if (tableLines.length === 0) return;
            const rows = tableLines.filter((l) => !/^\|?\s*-+\s*\|/.test(l));
            html += "<table>";
            rows.forEach((line, idx) => {
                const cells = line.split("|").map((c) => c.trim()).filter((c) => c.length > 0);
                if (cells.length === 0) return;
                const tag = idx === 0 ? "th" : "td";
                html += "<tr>" + cells.map((c) => `<${tag}>${c}</${tag}>`).join("") + "</tr>";
            });
            html += "</table>";
            tableLines = [];
        };

        for (const line of lines) {
            if (line.trim().startsWith("|")) {
                tableLines.push(line);
            } else {
                flushTable();
                html += line.length ? `<div>${line}</div>` : "<br>";
            }
        }
        flushTable();
        return html;
    }

    /**
     * Extract the SEATS marker from the agent reply if present.
     * Returns { cleanText, seatData } where seatData may be null.
     *
     * The agent should embed:
     *   <!--SEATS:{"trip_id":"abc123","available":[2,4,7],"total":44}-->
     * anywhere in its reply (typically at the end).
     */
    function extractSeatData(text) {
        const marker = /<!--SEATS:(\{.*?\})-->/s;
        const match  = text.match(marker);
        if (!match) return { cleanText: text, seatData: null };

        let seatData = null;
        try {
            seatData = JSON.parse(match[1]);
        } catch (_) { /* malformed — ignore */ }

        const cleanText = text.replace(marker, "").trim();
        return { cleanText, seatData };
    }

    // ── Message rendering ──────────────────────────────────────────────

    function addMessage(role, text) {
        const el = document.createElement("div");
        el.className = `message ${role}`;
        if (role === "agent") {
            el.innerHTML = formatReply(text);
        } else {
            el.textContent = text;
        }
        messageList.appendChild(el);
        messageList.scrollTop = messageList.scrollHeight;
    }

    // ── Seat map widget ────────────────────────────────────────────────

    /**
     * Render an interactive bus seat map and append it to the message list.
     * Clones structure from the <template> elements in the HTML rather than
     * building markup strings in JS — easier to restyle and inspect in DevTools.
     *
     * @param {object} seatData  { trip_id, available: number[], total: number }
     */
    function renderSeatMap(seatData) {
        const { available = [], total = 44 } = seatData;
        const availableSet = new Set(available.map(Number));

        let selectedSeat = null;

        // ── Clone widget shell from <template id="tmpl-seat-map"> ───
        const tmplWidget = document.getElementById("tmpl-seat-map");
        const widget     = tmplWidget.content.cloneNode(true).firstElementChild;

        // data-ref hooks replace fragile id-based queries
        const ref  = (name) => widget.querySelector(`[data-ref="${name}"]`);
        const grid    = ref("grid");
        const label   = ref("label");
        const confirm = ref("confirm");

        // ── Build rows from <template id="tmpl-seat-row"> ───────────
        const tmplRow  = document.getElementById("tmpl-seat-row");
        const tmplBtn  = document.getElementById("tmpl-seat-btn");
        const numRows  = Math.ceil(total / 4);
        const seatButtons = {};

        // Maps data-seat letter to column position in the 4-across layout
        const COL_ORDER = ["A", "B", "C", "D"];

        for (let row = 1; row <= numRows; row++) {
            const rowEl = tmplRow.content.cloneNode(true).firstElementChild;
            rowEl.querySelector("[data-ref='rowNum']").textContent = row;

            COL_ORDER.forEach((letter, i) => {
                const slot   = rowEl.querySelector(`[data-seat="${letter}"]`);
                const seatNum = (row - 1) * 4 + i + 1;

                if (seatNum > total) {
                    // Past the last seat — leave slot as an empty spacer
                    return;
                }

                const isAvail = availableSet.has(seatNum);

                // Clone seat button from <template id="tmpl-seat-btn">
                const btn = tmplBtn.content.cloneNode(true).firstElementChild;
                btn.classList.add(isAvail ? "available" : "taken");
                btn.textContent = seatNum;
                btn.disabled    = !isAvail;
                btn.title       = isAvail
                    ? `Seat ${seatNum} — available`
                    : `Seat ${seatNum} — taken`;

                btn.addEventListener("click", () => {
                    if (!availableSet.has(seatNum)) return;

                    // Deselect previous
                    if (selectedSeat !== null && seatButtons[selectedSeat]) {
                        seatButtons[selectedSeat].classList.remove("selected");
                        seatButtons[selectedSeat].classList.add("available");
                    }

                    // Select this one
                    selectedSeat = seatNum;
                    btn.classList.remove("available");
                    btn.classList.add("selected");

                    label.innerHTML = `You picked <strong>seat ${seatNum}</strong>.`;
                    confirm.disabled = false;
                });

                seatButtons[seatNum] = btn;
                slot.replaceWith(btn);   // swap placeholder div with real button
            });

            grid.appendChild(rowEl);
        }

        // ── Confirm button → auto-send message ─────────────────────
        confirm.addEventListener("click", () => {
            if (selectedSeat === null) return;

            // Lock the widget so it can't be double-clicked
            confirm.disabled = true;
            widget.querySelectorAll(".seat-btn").forEach(b => b.disabled = true);
            label.innerHTML = `Booking <strong>seat ${selectedSeat}</strong>…`;

            // Inject into chat as if the user typed it
            const message = `I'd like to book seat ${selectedSeat}`;
            messageInput.value = "";
            sendMessage(message);
        });

        messageList.appendChild(widget);
        messageList.scrollTop = messageList.scrollHeight;
    }

    // ── UI state ───────────────────────────────────────────────────────

    function setSending(isSending) {
        sendButton.disabled    = isSending;
        messageInput.disabled  = isSending;
        typingIndicator.classList.toggle("hidden", !isSending);
        if (isSending) messageList.scrollTop = messageList.scrollHeight;
    }

    // ── Core send / receive ────────────────────────────────────────────

    async function sendMessage(message) {
        addMessage("user", message);
        setSending(true);

        try {
            const response = await fetch(CHAT_ENDPOINT, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    traveler_external_id: travelerExternalId,
                    message,
                }),
            });

            const data = await response.json();

            if (!response.ok) {
                addMessage("error", data.error || "Something went wrong. Please try again.");
                return;
            }

            const rawReply = data.reply || "(no reply)";
            const { cleanText, seatData } = extractSeatData(rawReply);

            addMessage("agent", cleanText);

            if (seatData) {
                renderSeatMap(seatData);
            }

        } catch (err) {
            addMessage("error", "Could not reach SmartBus. Check your connection and try again.");
        } finally {
            setSending(false);
            messageInput.focus();
        }
    }

    // ── Entry screen ───────────────────────────────────────────────────

    startButton.addEventListener("click", () => {
        const normalized = normalizePhone(phoneInput.value);
        if (!normalized) {
            entryError.textContent = "Enter a valid phone number (digits only, optional leading +).";
            return;
        }
        entryError.textContent  = "";
        travelerExternalId      = normalized;
        travelerIdDisplay.textContent = normalized;

        entryScreen.classList.add("hidden");
        chatScreen.classList.remove("hidden");
        messageInput.focus();

        addMessage(
            "agent",
            "Hi! I'm SmartBus, your AI travel assistant. Tell me where you'd like to go — " +
            "for example, \"I want a bus from Kigali to Musanze tomorrow.\""
        );
    });

    phoneInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); startButton.click(); }
    });

    chatForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const message = messageInput.value.trim();
        if (!message) return;
        messageInput.value = "";
        sendMessage(message);
    });
})();