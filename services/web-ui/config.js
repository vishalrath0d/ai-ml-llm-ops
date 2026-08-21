// Base URLs the browser calls directly (CORS is enabled on all four
// services specifically so this works without a reverse proxy). Edit these
// if you've remapped any host ports in docker-compose.yml.
window.SERVICES = {
  llmGateway: "http://localhost:8001",
  ragService: "http://localhost:8002",
  agentService: "http://localhost:8003",
  evalService: "http://localhost:8004",
};

// No client-side abort timeout by default — CPU-only local inference can
// legitimately take 60-180s+ per call (see docker-compose.yml comments on
// LLM_REQUEST_TIMEOUT_SECONDS / HTTP_TIMEOUT_SECONDS). Set a number of
// milliseconds here if you want the UI to give up after a fixed time.
window.REQUEST_TIMEOUT_MS = null;
