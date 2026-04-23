const config = window.TICKET_CONSOLE_CONFIG || {};
const apiBaseUrl = (config.API_BASE_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
const forceMock = new URLSearchParams(window.location.search).get("mock") === "true";
let useMockApi = forceMock || Boolean(config.USE_MOCK_API);

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
const categoryValue = document.querySelector("#category-value");
const modelValue = document.querySelector("#model-value");
const segments = document.querySelectorAll("[data-view-target]");
const views = document.querySelectorAll("[data-view]");
const ticketCount = document.querySelector("#ticket-count");
const ticketList = document.querySelector("#ticket-list");
const ticketsEmpty = document.querySelector("#tickets-empty");
const clearTicketsButton = document.querySelector("#clear-tickets-button");

const priorityClasses = ["low", "medium", "high", "urgent", "unknown"];
const priorityRank = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
  unknown: 4,
};
const storageKey = "ticket-console-ingested-tickets";
let ingestedTickets = loadTickets();

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

function loadTickets() {
  try {
    return JSON.parse(localStorage.getItem(storageKey)) || [];
  } catch (_error) {
    return [];
  }
}

function saveTickets() {
  localStorage.setItem(storageKey, JSON.stringify(ingestedTickets));
}

function shortDate(timestamp) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function truncateText(text, maxLength = 220) {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength).trim()}...`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderTickets() {
  const sortedTickets = [...ingestedTickets].sort((a, b) => {
    const aPriority = normalizePriority(a.priority);
    const bPriority = normalizePriority(b.priority);
    return priorityRank[aPriority] - priorityRank[bPriority] || b.createdAt - a.createdAt;
  });

  ticketCount.textContent = String(sortedTickets.length);
  ticketsEmpty.classList.toggle("hidden", sortedTickets.length > 0);
  ticketList.innerHTML = "";

  sortedTickets.forEach((ticket) => {
    const priority = normalizePriority(ticket.priority);
    const item = document.createElement("article");
    item.className = `ticket-item ticket-item--${priority}`;
    item.innerHTML = `
      <div class="ticket-item__meta">
        <span class="priority-chip">${formatPriority(priority)}</span>
        <span>${shortDate(ticket.createdAt)}</span>
      </div>
      <p>${escapeHtml(truncateText(ticket.text))}</p>
      <div class="ticket-item__footer">
        <span>${Math.round(ticket.confidence * 100)}% confidence</span>
        <span>${escapeHtml(ticket.model_version || "unknown model")}</span>
      </div>
    `;
    ticketList.appendChild(item);
  });
}

function ingestTicket(text, prediction) {
  ingestedTickets.push({
    id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
    text,
    createdAt: Date.now(),
    priority: prediction.priority,
    confidence: typeof prediction.confidence === "number" ? prediction.confidence : 0,
    category: prediction.category || "ticket_classification",
    model_version: prediction.model_version || "unknown",
  });
  saveTickets();
  renderTickets();
}

function mockPredict(text) {
  const lower = text.toLowerCase();
  let priority = "low";

  if (lower.includes("today") || lower.includes("immediately") || lower.includes("urgent")) {
    priority = "urgent";
  } else if (lower.includes("refund") || lower.includes("charged") || lower.includes("failed")) {
    priority = "high";
  } else if (lower.includes("delayed") || lower.includes("login") || lower.includes("otp")) {
    priority = "medium";
  }

  return {
    priority,
    category: "ticket_classification",
    confidence: priority === "urgent" ? 0.94 : 0.87,
    model_version: "mock-ui-v1",
  };
}

async function checkHealth() {
  setModeLabel();

  if (useMockApi) {
    setHealth("Mock mode active", "mock");
    return;
  }

  try {
    const response = await fetch(`${apiBaseUrl}/health`);
    if (!response.ok) {
      throw new Error(`Health check failed with ${response.status}`);
    }
    const payload = await response.json();
    setHealth(payload.model_loaded ? "Online, model loaded" : "Online, model pending", "ok");
  } catch (error) {
    useMockApi = true;
    setModeLabel();
    setHealth("Offline, using mock mode", "mock");
    console.warn("Health check failed; falling back to mock mode.", error);
  }
}

async function predictTicket(text) {
  if (useMockApi) {
    await new Promise((resolve) => setTimeout(resolve, 450));
    return mockPredict(text);
  }

  const response = await fetch(`${apiBaseUrl}/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) {
    throw new Error(`Prediction failed with ${response.status}`);
  }

  return response.json();
}

function renderPrediction(prediction) {
  const normalized = normalizePriority(prediction.priority);
  emptyState.classList.add("hidden");
  resultCard.classList.remove("hidden");
  priorityClasses.forEach((name) => resultCard.classList.remove(`result-card--${name}`));
  resultCard.classList.add(`result-card--${normalized}`);
  priorityBand.textContent = normalized;
  priorityValue.textContent = formatPriority(prediction.priority);
  confidenceValue.textContent =
    typeof prediction.confidence === "number"
      ? `${Math.round(prediction.confidence * 100)}%`
      : "--";
  categoryValue.textContent = prediction.category || "--";
  modelValue.textContent = prediction.model_version || "--";
}

function renderError(message) {
  emptyState.classList.add("hidden");
  resultCard.classList.remove("hidden");
  priorityClasses.forEach((name) => resultCard.classList.remove(`result-card--${name}`));
  resultCard.classList.add("result-card--unknown");
  priorityBand.textContent = "error";
  priorityValue.textContent = "Could not classify";
  confidenceValue.textContent = "--";
  categoryValue.textContent = message;
  modelValue.textContent = useMockApi ? "mock-ui-v1" : "live-api";
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
    const prediction = await predictTicket(text);
    renderPrediction(prediction);
    ingestTicket(text, prediction);
  } catch (error) {
    renderError(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Predict Priority";
  }
});

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

clearTicketsButton.addEventListener("click", () => {
  ingestedTickets = [];
  saveTickets();
  renderTickets();
});

updateCharacterCount();
renderTickets();
checkHealth();
