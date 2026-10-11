// Клиент API стенда (online/api.py). Ключи доступа хранятся в браузере и добавляются к запросам.

const KEYS = { admin: "shekara-admin", operator: "shekara-operator" };

function read(key) {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}

function write(key, value) {
  try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch { /* приватный режим */ }
}

export const auth = {
  role: "",
  get adminToken() { return read(KEYS.admin); },
  set adminToken(value) { write(KEYS.admin, value); },
  get operatorToken() { return read(KEYS.operator); },
  set operatorToken(value) { write(KEYS.operator, value); },
};

function headers() {
  const result = { "Content-Type": "application/json" };
  if (auth.role) result["X-Role"] = auth.role;
  if (auth.adminToken) result["X-Admin-Token"] = auth.adminToken;
  if (auth.operatorToken) result["X-Operator-Token"] = auth.operatorToken;
  return result;
}

async function request(method, path, body) {
  const response = await fetch(path, { method, headers: headers(), body: body ? JSON.stringify(body) : undefined });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Ошибка ${response.status}`);
  return data;
}

export const api = {
  config: () => request("GET", "/v1/config"),
  simState: () => request("GET", "/v1/sim/state"),
  simEvents: since => request("GET", `/v1/sim/events?since=${since}`),
  simControl: command => request("POST", "/v1/sim/control", command),

  banks: () => request("GET", "/v1/banks"),
  bankQueue: bank => request("GET", `/v1/banks/${encodeURIComponent(bank)}/queue`),
  bankFeed: bank => request("GET", `/v1/banks/${encodeURIComponent(bank)}/feed`),
  account: (bank, account) => request("GET", `/v1/banks/${encodeURIComponent(bank)}/accounts/${encodeURIComponent(account)}`),
  queryCounterparty: (bank, rowNo) => request("POST", `/v1/banks/${encodeURIComponent(bank)}/transfers/${rowNo}/query`),
  openIncident: (bank, account, kind = "confirmed") =>
    request("POST", `/v1/banks/${encodeURIComponent(bank)}/accounts/${encodeURIComponent(account)}/incident`, { kind }),

  incidents: () => request("GET", "/v1/operator/incidents"),
  incidentTree: id => request("GET", `/v1/operator/incidents/${encodeURIComponent(id)}/full-tree`),

  auditHead: () => request("GET", "/v1/audit/head"),
  auditEntries: (limit = 60) => request("GET", `/v1/audit/entries?limit=${limit}`),
  auditVerify: () => request("GET", "/v1/audit/verify"),
  auditVerifySignature: head => request("POST", "/v1/audit/verify-signature", head),

  results: () => request("GET", "/v1/results"),
};
