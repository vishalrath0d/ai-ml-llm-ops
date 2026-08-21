// Plain vanilla JS, no build step, no framework — kept deliberately simple
// so this project doesn't need an npm install/build stage on top of
// everything else. See config.js for the backend base URLs.
const S = window.SERVICES;

// ---------- small fetch helper with an elapsed-time callback ----------
async function callApi(url, opts, onTick) {
  const start = Date.now();
  const tick = onTick ? setInterval(() => onTick(Date.now() - start), 500) : null;
  try {
    const controller = window.REQUEST_TIMEOUT_MS ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), window.REQUEST_TIMEOUT_MS) : null;
    const res = await fetch(url, { ...opts, signal: controller ? controller.signal : undefined });
    if (timer) clearTimeout(timer);
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }
    if (!res.ok) {
      const detail = (data && data.detail) ? data.detail : text;
      throw new Error(`HTTP ${res.status}: ${detail}`);
    }
    return data;
  } finally {
    if (tick) clearInterval(tick);
  }
}

function fmtSecs(ms) { return (ms / 1000).toFixed(1) + "s"; }

// ---------- tabs ----------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// ---------- health strip ----------
async function refreshHealth() {
  const targets = [
    ["llm-gateway", S.llmGateway],
    ["rag-service", S.ragService],
    ["agent-service", S.agentService],
    ["eval-service", S.evalService],
  ];
  const strip = document.getElementById("health-strip");
  strip.innerHTML = targets.map(([name]) => `<span class="health-pill" id="hp-${name}">${name}: checking…</span>`).join("");
  for (const [name, base] of targets) {
    const el = document.getElementById(`hp-${name}`);
    try {
      const res = await fetch(base + "/health", { cache: "no-store" });
      el.textContent = `${name}: ${res.ok ? "up" : "error " + res.status}`;
      el.className = "health-pill " + (res.ok ? "ok" : "bad");
    } catch {
      el.textContent = `${name}: unreachable`;
      el.className = "health-pill bad";
    }
  }
}
refreshHealth();
setInterval(refreshHealth, 30000);

// ===================== CHAT =====================
const transcript = document.getElementById("chat-transcript");
const toolsPanel = document.getElementById("chat-tools");
const urgencyPanel = document.getElementById("chat-urgency");

function appendMsg(role, text, cls) {
  const div = document.createElement("div");
  div.className = `msg ${role} ${cls || ""}`.trim();
  div.innerHTML = `<div class="role">${role}</div><div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
  return div;
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sessionId = document.getElementById("chat-session").value.trim() || "ui-session-1";
  const customerId = document.getElementById("chat-customer-id").value.trim() || null;
  const input = document.getElementById("chat-message");
  const message = input.value.trim();
  if (!message) return;
  const sendBtn = document.getElementById("chat-send");

  appendMsg("user", customerId ? `${message}  [customer_id: ${customerId}]` : message);
  input.value = "";
  sendBtn.disabled = true;
  const pending = appendMsg("assistant", "waiting for response… 0.0s", "pending");

  try {
    const data = await callApi(
      `${S.agentService}/chat`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId, message, customer_id: customerId }) },
      (elapsed) => { pending.querySelector(".bubble").textContent = `waiting for response… ${fmtSecs(elapsed)} (CPU-only local inference is slow, this is expected)`; }
    );
    pending.remove();
    appendMsg("assistant", data.response || JSON.stringify(data));
    const calls = data.tool_calls || [];
    toolsPanel.innerHTML = calls.length
      ? calls.map((t) => `<span class="tool-pill">${typeof t === "string" ? t : (t.tool || t.name || JSON.stringify(t))}</span>`).join("")
      : `<span class="hint">none this turn — the model answered directly (the free local model doesn't always reach for tools reliably; see README)</span>`;

    const urgency = data.urgency || "unknown";
    const version = data.urgency_model_version;
    urgencyPanel.innerHTML = version
      ? `<span class="tool-pill">${urgency}</span> <span class="hint">MLflow model v${version}</span>`
      : `<span class="hint">unknown — no MLflow model loaded yet. Run services/mlflow/run_training.sh, then POST /admin/reload-model.</span>`;
  } catch (err) {
    pending.remove();
    appendMsg("assistant", "Error: " + err.message, "error");
  } finally {
    sendBtn.disabled = false;
  }
});

// ===================== KNOWLEDGE BASE =====================
document.getElementById("ingest-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = document.getElementById("ingest-result");
  box.className = "result-box"; box.textContent = "ingesting…";
  try {
    const data = await callApi(`${S.ragService}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: document.getElementById("ingest-text").value,
        source: document.getElementById("ingest-source").value || "web-ui",
      }),
    });
    box.className = "result-box ok";
    box.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    box.className = "result-box err";
    box.textContent = "Error: " + err.message;
  }
});

document.getElementById("query-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = document.getElementById("query-result");
  box.className = "result-box"; box.textContent = "querying…";
  try {
    const data = await callApi(`${S.ragService}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: document.getElementById("query-text").value,
        mode: document.getElementById("query-mode").value,
        k: Number(document.getElementById("query-k").value) || 3,
      }),
    });
    box.className = "result-box ok";
    box.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    box.className = "result-box err";
    box.textContent = "Error: " + err.message;
  }
});

// ===================== EVALS =====================
let SCENARIOS_CACHE = [];

function renderScenarios() {
  const list = document.getElementById("scenarios-list");
  list.innerHTML = SCENARIOS_CACHE.map((s) => `
    <div class="scenario-card" id="scenario-${s.id}">
      <div class="row">
        <div>
          <h4>#${s.id} ${s.name}</h4>
          <p class="criteria"><strong>Opens with:</strong> "${s.opening_message}"</p>
          <p class="criteria"><strong>Success criteria:</strong> ${s.success_criteria}</p>
        </div>
        <button class="secondary run-scenario-btn" data-id="${s.id}">Run</button>
      </div>
      <div class="scenario-result" id="scenario-result-${s.id}"></div>
    </div>
  `).join("") || "<p class=\"hint\">No scenarios loaded.</p>";

  list.querySelectorAll(".run-scenario-btn").forEach((btn) => {
    btn.addEventListener("click", () => runScenario(Number(btn.dataset.id)));
  });
}

async function runScenario(id) {
  const resultEl = document.getElementById(`scenario-result-${id}`);
  const btn = document.querySelector(`.run-scenario-btn[data-id="${id}"]`);
  btn.disabled = true;
  resultEl.innerHTML = `<span class="spinner"></span> running (drives a real agent-service conversation + judge call — 1-3+ min on CPU-only local inference)… 0.0s`;
  try {
    const data = await callApi(
      `${S.evalService}/scenarios/${id}/run`,
      { method: "POST" },
      (elapsed) => { resultEl.innerHTML = `<span class="spinner"></span> running… ${fmtSecs(elapsed)}`; }
    );
    const badge = data.result === "pass" ? "pass" : "fail";
    resultEl.innerHTML = `
      <span class="badge ${badge}">${data.result} — ${data.score_percent}%</span>
      <p style="margin:8px 0 0">${data.reason}</p>
      <p class="hint">latency: ${fmtSecs(data.latency_ms)}</p>
    `;
  } catch (err) {
    resultEl.innerHTML = `<span class="badge fail">error</span> <span style="color:var(--danger)">${err.message}</span>`;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("evals-refresh").addEventListener("click", async () => {
  const status = document.getElementById("evals-status");
  status.textContent = "loading…";
  try {
    SCENARIOS_CACHE = await callApi(`${S.evalService}/scenarios`, {});
    renderScenarios();
    status.textContent = `${SCENARIOS_CACHE.length} scenario(s) loaded`;
    await refreshEvalMetrics();
  } catch (err) {
    status.textContent = "Error: " + err.message;
  }
});

document.getElementById("evals-run-all").addEventListener("click", async () => {
  if (!confirm("This runs every seeded scenario against agent-service + a judge call each. On CPU-only local inference this can take 15-20+ minutes. Continue?")) return;
  const status = document.getElementById("evals-status");
  const btn = document.getElementById("evals-run-all");
  btn.disabled = true;
  status.innerHTML = `<span class="spinner"></span> running all scenarios… 0.0s`;
  try {
    await callApi(
      `${S.evalService}/scenarios/run-all`,
      { method: "POST" },
      (elapsed) => { status.innerHTML = `<span class="spinner"></span> running all scenarios… ${fmtSecs(elapsed)}`; }
    );
    status.textContent = "done — refresh scenarios to see results";
    await refreshEvalMetrics();
  } catch (err) {
    status.textContent = "Error: " + err.message;
  } finally {
    btn.disabled = false;
  }
});

async function refreshEvalMetrics() {
  const box = document.getElementById("evals-metrics");
  try {
    const res = await fetch(`${S.evalService}/metrics`);
    const text = await res.text();
    const relevant = text.split("\n").filter((l) => l.startsWith("eval_") && !l.startsWith("#")).join("\n");
    box.textContent = relevant || "(no eval metrics yet — run a scenario first)";
  } catch (err) {
    box.textContent = "Error: " + err.message;
  }
}

// ===================== GATEWAY / CHAOS =====================
document.getElementById("raw-chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const box = document.getElementById("raw-chat-result");
  box.className = "result-box"; box.textContent = "sending… 0.0s";
  try {
    const data = await callApi(
      `${S.llmGateway}/v1/chat/completions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: document.getElementById("raw-model").value,
          messages: [{ role: "user", content: document.getElementById("raw-message").value }],
        }),
      },
      (elapsed) => { box.textContent = `sending… ${fmtSecs(elapsed)}`; }
    );
    box.className = "result-box ok";
    box.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    box.className = "result-box err";
    box.textContent = "Error: " + err.message;
  }
});

document.getElementById("chaos-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await applyChaos(
    Number(document.getElementById("chaos-latency").value) || 0,
    Number(document.getElementById("chaos-error").value) || 0
  );
});
document.getElementById("chaos-reset").addEventListener("click", async () => {
  document.getElementById("chaos-latency").value = 0;
  document.getElementById("chaos-error").value = 0;
  await applyChaos(0, 0);
});
document.getElementById("chaos-refresh").addEventListener("click", async () => {
  const box = document.getElementById("chaos-result");
  try {
    const data = await callApi(`${S.llmGateway}/admin/chaos`, {});
    box.className = "result-box ok";
    box.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    box.className = "result-box err";
    box.textContent = "Error: " + err.message;
  }
});
async function applyChaos(latencyMs, errorRate) {
  const box = document.getElementById("chaos-result");
  box.className = "result-box"; box.textContent = "applying…";
  try {
    const data = await callApi(`${S.llmGateway}/admin/chaos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latency_ms: latencyMs, error_rate: errorRate }),
    });
    box.className = "result-box ok";
    box.textContent = "Applied:\n" + JSON.stringify(data, null, 2);
  } catch (err) {
    box.className = "result-box err";
    box.textContent = "Error: " + err.message;
  }
}

// Load scenarios once on first paint so the Evals tab isn't empty.
document.getElementById("evals-refresh").click();
