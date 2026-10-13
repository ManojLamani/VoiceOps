const API_BASE = '/api';
const TOKEN_KEY = 'voiceops_token';

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

class ApiService {
  constructor() {
    this.token = readToken();
    this.onUnauthorized = null;
  }

  setToken(token) {
    this.token = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch { /* storage unavailable: keep the in-memory token */ }
  }

  getToken() {
    return this.token;
  }

  async request(url, options = {}) {
    const headers = { ...options.headers };
    if (!(options.body instanceof FormData) && options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (this.token) headers.Authorization = `Bearer ${this.token}`;

    let response;
    try {
      response = await fetch(`${API_BASE}${url}`, { ...options, headers });
    } catch {
      throw new ApiError('Cannot reach the VoiceOps server. Check that the backend is running.', 0);
    }
    if (response.status === 401 && !url.startsWith('/auth/login')) {
      this.setToken(null);
      this.onUnauthorized?.();
      throw new ApiError('Your session has expired. Please sign in again.', 401);
    }
    if (!response.ok) {
      if ([502, 503, 504].includes(response.status)) {
        throw new ApiError('The VoiceOps server is temporarily unavailable. Please try again in a moment.', response.status);
      }
      const body = await response.json().catch(() => ({}));
      const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(', ') : body.detail;
      throw new ApiError(detail || `Request failed (${response.status})`, response.status);
    }
    return response.status === 204 ? null : response.json();
  }

  get(url) { return this.request(url); }
  post(url, data) { return this.request(url, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }); }
  put(url, data) { return this.request(url, { method: 'PUT', body: JSON.stringify(data) }); }
  patch(url, data) { return this.request(url, { method: 'PATCH', body: JSON.stringify(data) }); }

  // Auth
  async login(email, password) {
    const data = await this.post('/auth/login', { email, password });
    this.setToken(data.access_token);
    return data;
  }
  async register(email, password, full_name) {
    const data = await this.post('/auth/register', { email, password, full_name });
    this.setToken(data.access_token);
    return data;
  }
  getMe() { return this.get('/auth/me'); }
  logout() { this.setToken(null); }

  // Conversations
  createConversation(customerId = null, channel = 'voice') {
    return this.post('/conversations', { customer_id: customerId, channel });
  }
  getConversations({ skip = 0, limit = 50, status } = {}) {
    const q = new URLSearchParams({ skip, limit, ...(status ? { status } : {}) });
    return this.get(`/conversations?${q}`);
  }
  getConversation(id) { return this.get(`/conversations/${id}`); }
  sendMessage(id, content) { return this.post(`/conversations/${id}/messages`, { content }); }
  endConversation(id) { return this.post(`/conversations/${id}/end`); }

  // Voice (REST fallback when the WebSocket is unavailable)
  voiceRespond(conversationId, { text, audio } = {}) {
    const form = new FormData();
    form.append('conversation_id', conversationId);
    if (text) form.append('text', text);
    if (audio) form.append('audio', audio, 'utterance.wav');
    return this.request('/voice/respond', { method: 'POST', body: form });
  }

  // Data
  getCustomers() { return this.get('/customers?limit=200'); }
  getCustomerOrders(customerId) { return this.get(`/customers/${customerId}/orders`); }
  getOrders(status) { return this.get(`/orders?limit=200${status ? `&status=${status}` : ''}`); }
  getTickets() { return this.get('/tickets?limit=100'); }
  updateTicket(ticketId, data) { return this.put(`/tickets/${ticketId}`, data); }
  getEscalations() { return this.get('/escalations?limit=100'); }
  updateEscalation(id, status) { return this.patch(`/escalations/${id}`, { status }); }

  // Knowledge
  getKnowledgeDocs() { return this.get('/knowledge?limit=200'); }
  ingestKnowledge() { return this.post('/knowledge/ingest'); }
  searchKnowledge(query, topK = 5) {
    return this.get(`/knowledge/search?query=${encodeURIComponent(query)}&top_k=${topK}`);
  }

  // Analytics
  getAnalyticsOverview() { return this.get('/analytics/overview'); }
  getConversationsOverTime(days = 14) { return this.get(`/analytics/conversations-over-time?days=${days}`); }
  getIntentDistribution() { return this.get('/analytics/intents'); }
  getToolUsage() { return this.get('/analytics/tool-usage'); }
  getLatency(limit = 50) { return this.get(`/analytics/latency?limit=${limit}`); }
  getEscalationReasons() { return this.get('/analytics/escalation-reasons'); }
  getSentiment() { return this.get('/analytics/sentiment'); }

  // System
  getSystemStatus() { return this.get('/system/status'); }
}

export const api = new ApiService();
export default api;
