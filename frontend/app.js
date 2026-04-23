const config = window.TICKET_CONSOLE_CONFIG || {};
const apiBaseUrl = (config.API_BASE_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
const forceMock = new URLSearchParams(window.location.search).get("mock") === "true";
let useMockApi = forceMock || Boolean(config.USE_MOCK_API);

// ---------- DOM ----------
const authGate = document.querySelector("#auth-gate");
const appMain = document.querySelector("#app-main");
const userBar = document.querySelector("#user-bar");
const userEmailEl = document.querySelector("#user-email");
const btnSignIn = document.querySelector("#btn-sign-in");
const btnSignOut = document.querySelector("#btn-sign-out");
const authError = document.querySelector("#auth-error");
const toastContainer = document.querySelector("#toast-container");

const form = document.querySelector("#ticket-form");
const textarea = document.querySelector("#ticket-text");
const clearButton = document.querySelector("#clear-button");
const submitButton = document.querySelector("#submit-button");
const characterCount = document.querySelector("#character-count");
const healthStatus = document.querySelector("#health-status");
const statusDot = document.querySelector("#status-dot");
const modePill = document.querySelector("#mode-pill");
const emptyState = document.querySelector("#empty-state");
const resultCard = document.querySelector("#result-card");
const priorityBand = document.querySelector("#priority-band");
const priorityValue = document.querySelector("#priority-value");
const confidenceValue = document.querySelector("#confidence-value");
const modelValue = document.querySelector("#model-value");
const ticketIdValue = document.querySelector("#ticket-id-value");
const feedbackRow = document.querySelector("#feedback-row");
const feedbackUp = document.querySelector("#feedback-up");
const feedbackDown = document.querySelector("#feedback-down");
const feedbackThanks = document.querySelector("#feedback-thanks");
const segments = document.querySelectorAll("[data-view-target]");
const views = document.querySelectorAll("[data-view]");
const ticketCount = document.querySelector("#ticket-count");
const ticketList = document.querySelector("#ticket-list");
const ticketsEmpty = document.querySelector("#tickets-empty");
const refreshTicketsButton = document.querySelector("#refresh-tickets-button");

const priorityClasses = ["low", "medium", "high", "urgent", "unknown"];

let currentPredictionId = null;
let cachedTickets = [];
let currentUser = null;
let appBooted = false;

// ---------- Mock state ----------
const mockTickets = [
  {
    ticket_id: "mock-ticket-1",
    prediction_id: "mock-pred-1",
    ticket_text: "I was double charged on my latest order and need a refund today.",
    predicted_priority: "urgent",
    confidence: 0.94,
    all_scores: { low: 0.01, medium: 0.02, high: 0.03, urgent: 0.94 },
    model_version: "mock",
    model_run_id: "mock",
    latency_ms: 25,
    created_at: new Date().toISOString(),
  },
  {
    ticket_id: "mock-ticket-2",
    prediction_id: "mock-pred-2",
    ticket_text: "My app won't let me log in after the latest update.",
    predicted_priority: "medium",
    confidence: 0.75,
    all_scores: { low: 0.1, medium: 0.75, high: 0.1, urgent: 0.05 },
    model_version: "mock",
    model_run_id: "mock",
    latency_ms: 20,
    created_at: new Date(Date.now() - 3600_000).toISOString(),
  },
];

// ---------- Utilities ----------
function setModeLabel() {
  modePill.textContent = useMockApi ? "Mock API" : "Live API";
}

function updateCharacterCount() {
  const count = textarea.value.length;
  characterCount.textContent = `${count} character${count === 1 ? "" : "s"}`;
}

function setHealth(status, tone) {
  healthStatus.textContent = status;
  statusDot.className = `status-dot status-dot--${tone}`;
}

function normalizePriority(priority) {
  const clean = String(priority || "unknown").toLowerCase().replace("_priority", "");
  return priorityClasses.includes(clean) ? clean : "unknown";
}

function formatPriority(priority) {
  const normalized = normalizePriority(priority);
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function switchView(viewName) {
  segments.forEach((segment) => {
    segment.classList.toggle("segment--active", segment.dataset.viewTarget === viewName);
  });
  views.forEach((view) => {
    view.classList.toggle("view--active", view.dataset.view === viewName);
  });
}

function shortDate(isoOrEpoch) {
  const d = typeof isoOrEpoch === "number" ? new Date(isoOrEpoch) : new Date(isoOrEpoch);
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
}

function truncateText(text, maxLength = 220) {
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trim()}...`;
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(message, tone = "info", ttl = 4000) {
  if (!toastContainer) return;
  const el = document.createElement("div");
  el.className = `toast toast--${tone}`;
  el.textContent = message;
  toastContainer.appendChild(el);
  setTimeout(() => {
    el.classList.add("toast--fade");
    setTimeout(() => el.remove(), 300);
  }, ttl);
}

// ---------- authedFetch ----------
async function authedFetch(path, options = {}) {
  const url = path.startsWith("http") ? path : `${apiBaseUrl}${path}`;
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (!useMockApi) {
    const fb = window.__firebase;
    if (fb) {
      try {
        const token = await fb.getIdToken();
        if (token) {
          headers.set("Authorization", `Bearer ${token}`);
        }
      } catch (err) {
        console.warn("Failed to get ID token", err);
      }
    }
  }

  const response = await fetch(url, { ...options, headers });

  if (!useMockApi) {
    if (response.status === 401) {
      showToast("Session expired. Please sign in again.", "danger");
      try {
        if (window.__firebase) await window.__firebase.signOut();
      } catch (_) { /* ignore */ }
      throw new Error("unauthorized");
    }
    if (response.status === 429) {
      showToast("Slow down — limit is 50 req/min.", "warn");
      throw new Error("rate_limited");
    }
  }

  return response;
}

// ---------- API calls ----------
async function apiHealth() {
  if (useMockApi) {
    return { status: "ok", model_version: "mock", model_run_id: "mock" };
  }
  // /health is public — no auth required, but harmless to go through authedFetch.
  const response = await fetch(`${apiBaseUrl}/health`);
  if (!response.ok) throw new Error(`Health check failed with ${response.status}`);
  return response.json();
}

async function apiMe() {
  if (useMockApi) {
    return { uid: "mock-user", email: "mock@example.com", display_name: "Mock User" };
  }
  const response = await authedFetch(`/me`);
  if (!response.ok) throw new Error(`/me failed with ${response.status}`);
  return response.json();
}

async function apiCreateTicket(ticketText) {
  if (useMockApi) {
    await new Promise((r) => setTimeout(r, 400));
    const id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
    const predId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + 1);
    const ticket = {
      ticket_id: id,
      prediction_id: predId,
      ticket_text: ticketText,
      predicted_priority: "medium",
      confidence: 0.75,
      all_scores: { low: 0.1, medium: 0.75, high: 0.1, urgent: 0.05 },
      model_version: "mock",
      model_run_id: "mock",
      latency_ms: 10,
      created_at: new Date().toISOString(),
    };
    mockTickets.unshift(ticket);
    return ticket;
  }

  const response = await authedFetch(`/tickets`, {
    method: "POST",
    body: JSON.stringify({ ticket_text: ticketText }),
  });

  if (!response.ok) {
    let detail = "";
    try { detail = JSON.stringify(await response.json()); } catch (_) { /* ignore */ }
    if (response.status === 502) {
      throw new Error("Model service is currently unavailable (502). Please try again shortly.");
    }
    throw new Error(`Create ticket failed with ${response.status}${detail ? `: ${detail}` : ""}`);
  }
  return response.json();
}

async function apiListTickets(limit = 50) {
  if (useMockApi) {
    return [...mockTickets].slice(0, limit);
  }
  const response = await authedFetch(`/tickets?limit=${limit}`);
  if (!response.ok) throw new Error(`List tickets failed with ${response.status}`);
  return response.json();
}

async function apiSendFeedback(predictionId, verdict) {
  if (useMockApi) {
    await new Promise((r) => setTimeout(r, 200));
    return {
      feedback_id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
      created_at: new Date().toISOString(),
    };
  }
  const response = await authedFetch(`/feedback`, {
    method: "POST",
    body: JSON.stringify({ prediction_id: predictionId, verdict }),
  });
  if (!response.ok) throw new Error(`Feedback failed with ${response.status}`);
  return response.json();
}

// ---------- Rendering ----------
function resetFeedbackRow() {
  feedbackRow.hidden = true;
  feedbackThanks.classList.add("hidden");
  feedbackUp.classList.remove("hidden", "feedback-btn--selected");
  feedbackDown.classList.remove("hidden", "feedback-btn--selected");
  feedbackUp.disabled = false;
  feedbackDown.disabled = false;
}

function renderPrediction(ticket) {
  const normalized = normalizePriority(ticket.predicted_priority);
  emptyState.classList.add("hidden");
  resultCard.classList.remove("hidden");
  priorityClasses.forEach((name) => resultCard.classList.remove(`result-card--${name}`));
  resultCard.classList.add(`result-card--${normalized}`);
  priorityBand.textContent = normalized;
  priorityValue.textContent = formatPriority(ticket.predicted_priority);
  confidenceValue.textContent =
    typeof ticket.confidence === "number"
      ? `${Math.round(ticket.confidence * 100)}%`
      : "--";
  modelValue.textContent = ticket.model_version ? `v${ticket.model_version}` : "--";
  ticketIdValue.textContent = ticket.ticket_id ? ticket.ticket_id.slice(0, 8) : "--";

  currentPredictionId = ticket.prediction_id || null;
  resetFeedbackRow();
  if (currentPredictionId) {
    feedbackRow.hidden = false;
  }
}

function renderError(message) {
  emptyState.classList.add("hidden");
  resultCard.classList.remove("hidden");
  priorityClasses.forEach((name) => resultCard.classList.remove(`result-card--${name}`));
  resultCard.classList.add("result-card--unknown");
  priorityBand.textContent = "error";
  priorityValue.textContent = "Could not classify";
  confidenceValue.textContent = "--";
  modelValue.textContent = useMockApi ? "mock" : "live-api";
  ticketIdValue.textContent = message;
  feedbackRow.hidden = true;
}

function renderTickets(tickets) {
  cachedTickets = tickets;
  ticketCount.textContent = String(tickets.length);
  ticketsEmpty.classList.toggle("hidden", tickets.length > 0);
  ticketList.innerHTML = "";

  tickets.forEach((t) => {
    const priority = normalizePriority(t.predicted_priority);
    const item = document.createElement("article");
    item.className = `ticket-item ticket-item--${priority}`;
    item.dataset.predictionId = t.prediction_id || "";

    const text = t.ticket_text || t.text || "";
    const confPct =
      typeof t.confidence === "number" ? `${Math.round(t.confidence * 100)}% confidence` : "--";
    const modelLabel = t.model_version ? `v${t.model_version}` : "unknown model";
    const textBlock = text
      ? `<p class="ticket-item__text">${escapeHtml(truncateText(text, 200))}</p>`
      : `<p class="ticket-item__text ticket-item__text--empty">(no ticket text)</p>`;

    item.innerHTML = `
      <div class="ticket-item__meta">
        <span class="priority-chip">${formatPriority(priority)}</span>
        <span>${escapeHtml(shortDate(t.created_at))}</span>
      </div>
      ${textBlock}
      <div class="ticket-item__footer">
        <span>${confPct}</span>
        <span>${escapeHtml(modelLabel)}</span>
      </div>
      <div class="feedback-row feedback-row--row">
        <div class="feedback-buttons">
          <button class="feedback-btn" type="button" data-verdict="thumbs_up">&#128077;</button>
          <button class="feedback-btn" type="button" data-verdict="thumbs_down">&#128078;</button>
        </div>
        <span class="feedback-thanks hidden">Thanks.</span>
      </div>
    `;

    const row = item.querySelector(".feedback-row");
    row.querySelectorAll(".feedback-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const predictionId = item.dataset.predictionId;
        if (!predictionId) return;
        const buttons = row.querySelectorAll(".feedback-btn");
        buttons.forEach((b) => (b.disabled = true));
        try {
          await apiSendFeedback(predictionId, btn.dataset.verdict);
          buttons.forEach((b) => {
            if (b !== btn) b.classList.add("hidden");
          });
          btn.classList.add("feedback-btn--selected");
          row.querySelector(".feedback-thanks").classList.remove("hidden");
        } catch (err) {
          buttons.forEach((b) => (b.disabled = false));
          console.warn("Feedback failed", err);
        }
      });
    });

    ticketList.appendChild(item);
  });
}

// ---------- Flow ----------
async function refreshTickets() {
  try {
    const tickets = await apiListTickets(50);
    renderTickets(tickets);
  } catch (err) {
    console.warn("Failed to list tickets", err);
    renderTickets([]);
  }
}

async function checkHealth() {
  setModeLabel();

  if (useMockApi) {
    setHealth("Mock mode active", "mock");
    return;
  }

  try {
    const payload = await apiHealth();
    const version = payload.model_version || "unknown";
    setHealth(`Online, model ${version}`, "ok");
  } catch (error) {
    setHealth("Endpoint unreachable", "mock");
    console.warn("Health check failed.", error);
  }
}

// ---------- Auth flow ----------
function showAuthGate() {
  authGate.hidden = false;
  appMain.hidden = true;
  if (userBar) userBar.hidden = true;
}

function showAppUI(user) {
  authGate.hidden = true;
  appMain.hidden = false;
  if (userBar) {
    userBar.hidden = false;
    userEmailEl.textContent = user?.email || user?.displayName || "signed in";
  }
}

async function onSignedIn(user) {
  currentUser = user;
  showAppUI(user);
  if (!appBooted) {
    appBooted = true;
    updateCharacterCount();
  }
  // Confirm token/session with backend /me, then run initial loads.
  try {
    if (!useMockApi) {
      const me = await apiMe();
      if (me && (me.display_name || me.email) && userEmailEl) {
        userEmailEl.textContent = me.email || me.display_name;
      }
    }
  } catch (err) {
    console.warn("/me check failed", err);
    // If /me failed with unauthorized, authedFetch already signed the user out.
    return;
  }

  await checkHealth();
  await refreshTickets();
}

function onSignedOut() {
  currentUser = null;
  showAuthGate();
}

function initAuth() {
  if (useMockApi) {
    // Synthesize a fake user and jump straight into the app.
    const fake = { uid: "mock-user", email: "mock@example.com", displayName: "Mock User" };
    onSignedIn(fake);
    return;
  }

  const bind = () => {
    const fb = window.__firebase;
    if (!fb) {
      // Firebase failed to load. Show the gate with an error.
      showAuthGate();
      if (authError) {
        authError.hidden = false;
        authError.textContent = "Authentication service failed to load. Check console.";
      }
      return;
    }
    fb.onAuthStateChanged((user) => {
      if (user) onSignedIn(user);
      else onSignedOut();
    });
  };

  if (window.__firebase) {
    bind();
  } else {
    window.addEventListener("firebase-ready", bind, { once: true });
    window.addEventListener("firebase-error", () => {
      showAuthGate();
      if (authError) {
        authError.hidden = false;
        authError.textContent = "Authentication service failed to load. Check console.";
      }
    }, { once: true });
  }
}

// ---------- Event wiring ----------
if (btnSignIn) {
  btnSignIn.addEventListener("click", async () => {
    if (useMockApi) return;
    const fb = window.__firebase;
    if (!fb) {
      showToast("Auth not ready. Try again in a moment.", "danger");
      return;
    }
    btnSignIn.disabled = true;
    try {
      await fb.signIn();
    } catch (err) {
      console.warn("Sign-in failed", err);
      if (authError) {
        authError.hidden = false;
        authError.textContent = `Sign-in failed: ${err?.message || err}`;
      }
    } finally {
      btnSignIn.disabled = false;
    }
  });
}

if (btnSignOut) {
  btnSignOut.addEventListener("click", async () => {
    if (useMockApi) {
      // In mock mode just reload to re-enter the fake user state.
      onSignedOut();
      setTimeout(() => onSignedIn({ uid: "mock-user", email: "mock@example.com", displayName: "Mock User" }), 50);
      return;
    }
    const fb = window.__firebase;
    if (!fb) return;
    try {
      await fb.signOut();
    } catch (err) {
      console.warn("Sign-out failed", err);
    }
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textarea.value.trim();
  if (text.length < 5) {
    textarea.focus();
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = "Classifying...";

  try {
    const ticket = await apiCreateTicket(text);
    renderPrediction(ticket);
    refreshTickets();
  } catch (error) {
    renderError(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Classify ticket";
  }
});

feedbackUp.addEventListener("click", () => sendCurrentFeedback("thumbs_up"));
feedbackDown.addEventListener("click", () => sendCurrentFeedback("thumbs_down"));

async function sendCurrentFeedback(verdict) {
  if (!currentPredictionId) return;
  feedbackUp.disabled = true;
  feedbackDown.disabled = true;
  try {
    await apiSendFeedback(currentPredictionId, verdict);
    if (verdict === "thumbs_up") {
      feedbackDown.classList.add("hidden");
      feedbackUp.classList.add("feedback-btn--selected");
    } else {
      feedbackUp.classList.add("hidden");
      feedbackDown.classList.add("feedback-btn--selected");
    }
    feedbackThanks.classList.remove("hidden");
  } catch (err) {
    console.warn("Feedback failed", err);
    feedbackUp.disabled = false;
    feedbackDown.disabled = false;
  }
}

clearButton.addEventListener("click", () => {
  textarea.value = "";
  updateCharacterCount();
  textarea.focus();
});

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    textarea.value = button.dataset.example;
    updateCharacterCount();
    textarea.focus();
  });
});

textarea.addEventListener("input", updateCharacterCount);

segments.forEach((segment) => {
  segment.addEventListener("click", () => switchView(segment.dataset.viewTarget));
});

refreshTicketsButton.addEventListener("click", () => {
  refreshTickets();
});

// ---------- Boot ----------
setModeLabel();
initAuth();
