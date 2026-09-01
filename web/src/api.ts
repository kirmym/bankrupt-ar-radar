/** API-клиент для бэкенда AR Radar. */
import axios from "axios";

import { API_KEY_STORAGE_KEY, canRetryApiKey, normalizeApiKey } from "./auth";

const baseURL = import.meta.env.VITE_API_URL || "/api/v1";

export const api = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
});

// Keep the production API key out of the compiled bundle.  Users can enter it
// once when they first submit feedback; sessionStorage limits its lifetime to
// the current browser tab.
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const key = window.sessionStorage.getItem(API_KEY_STORAGE_KEY);
    if (key) {
      config.headers = config.headers ?? {};
      config.headers["X-API-Key"] = key;
    }
  }
  return config;
});

// Protected API reads and mutations use the same one-time browser prompt.
// Limit the retry to one attempt so an invalid key cannot recurse forever.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error?.config as
      | (typeof error.config & { __arRadarAuthRetried?: boolean })
      | undefined;
    if (!canRetryApiKey(error?.response?.status, Boolean(config?.__arRadarAuthRetried))) {
      return Promise.reject(error);
    }
    let key = typeof window !== "undefined" ? window.sessionStorage.getItem(API_KEY_STORAGE_KEY) : null;
    if (!key && typeof window !== "undefined") {
      key = normalizeApiKey(window.prompt("Введите API-ключ AR Radar"));
      if (key) window.sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
    }
    if (!key) return Promise.reject(error);
    config.__arRadarAuthRetried = true;
    config.headers = config.headers ?? {};
    config.headers["X-API-Key"] = key;
    return api.request(config);
  },
);

export interface PriceInterval {
  seq: number;
  price: string;
  starts_at?: string;
  ends_at?: string;
  is_current: boolean;
}

export interface DebtorParty {
  inn?: string;
  name?: string;
  status?: string;
  address?: string;
  director_name?: string;
  revenue?: string;
  cash?: string;
  equity?: string;
  kad_as_defendant_count?: number;
  kad_bankruptcy_open?: boolean | null;
  fssp_sum?: string;
  fssp_uncollectible?: boolean | null;
  source_as_of?: string;
  source_checks?: PartySourceCheck[];
}

export interface DocumentRecord {
  id: number;
  kind?: string;
  title?: string;
  external_id?: string;
  url?: string;
  downloaded_at?: string;
  text?: string;
  extracted_facts?: Record<string, unknown>;
  processing_status: string;
  last_error?: string;
}

export interface ScoreSnapshot {
  id: number;
  score_class: "A" | "B" | "C" | "D";
  ev: string;
  ev_low?: string;
  ev_high?: string;
  max_bid?: string;
  scenario?: string;
  stop_factors: string[];
  gaps: string[];
  model_version: string;
  scored_at: string;
}

export interface PartySourceCheck {
  source: string;
  status: string;
  checked_at?: string;
  next_retry_at?: string;
  failures: number;
  source_url?: string;
  last_error?: string;
}

export interface Claim {
  id: number;
  kind: string;
  principal?: string;
  penalties?: string;
  currency: string;
  base_contract?: string;
  base_date?: string;
  due_date?: string;
  limitations_deadline?: string;
  il_issue_date?: string;
  il_present_deadline?: string;
  court_case_no?: string;
  has_judgment: boolean | null;
  has_writ: boolean | null;
  enforcement_alive: boolean | null;
  secured: boolean;
  assignment_forbidden: boolean;
  debtor_party?: DebtorParty;
  guarantor_party?: DebtorParty;
}

export interface Lot {
  id: number;
  guid: string;
  lot_no: number;
  title?: string;
  description_text?: string;
  is_receivable: boolean;
  data_state?: "ready" | "needs_review" | "blocked" | "stale" | "unscored" | "unknown";
  nominal_claimed?: string;
  start_price?: string;
  current_price?: string;
  current_interval_from?: string;
  current_interval_to?: string;
  cutoff_price?: string;
  score_class?: "A" | "B" | "C" | "D";
  score_ev?: string;
  score_ev_low?: string;
  score_ev_high?: string;
  score_scenario?: string;
  score_stop_factors: string[];
  score_gaps: string[];
  score_max_bid?: string;
  score_version?: string;
  score_updated_at?: string;
  price_schedule_status?: string;
  price_observed_at?: string;
  price_source?: string;
  trade_status?: string;
  applications_from?: string;
  applications_to?: string;
  source_name?: string;
  source_url?: string;
  participation_exclusion_reason?: string;
  price_intervals: PriceInterval[];
  claims: Claim[];
  documents?: DocumentRecord[];
  score_snapshots?: ScoreSnapshot[];
  created_at: string;
  updated_at: string;
  trade?: TradeBrief;
}

export interface TradeSourceRef {
  source: string;
  source_url: string;
  external_trade_id?: string;
  external_lot_id?: string;
  captured_at: string;
}

export interface TradeBrief {
  id: number;
  guid: string;
  efrsb_url?: string;
  etp_name?: string;
  etp_url?: string;
  trade_kind: string;
  status: string;
  applications_from?: string;
  applications_to?: string;
  source_refs: TradeSourceRef[];
}

export interface LotList {
  items: Lot[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface Feedback {
  id: number;
  lot_id: number;
  action: string;
  recovered_amount?: string;
  expense_amount?: string;
  outcome?: "in_progress" | "recovered" | "not_recovered";
  outcome_at?: string;
  note?: string;
  created_at: string;
}

export interface DashboardStats {
  total_lots: number;
  receivable_lots: number;
  scored_lots: number;
  class_a: number;
  class_b: number;
  class_c: number;
  class_d: number;
  alerts_sent_today: number;
  stale_scored_lots: number;
  last_ingest_at?: string;
  source_status: string;
  active_lots: number;
  excluded_lots: number;
  ready_recommendations: number;
  review_candidates: number;
  documents_total: number;
  documents_completed: number;
  documents_pending: number;
  documents_needs_review: number;
  documents_retrying: number;
}

export const lotsApi = {
  list: (params: Record<string, unknown> = {}) => api.get<LotList>("/lots", { params }),
  get: (id: number) => api.get<Lot>(`/lots/${id}`),
  assignDebtor: (id: number, payload: { inn: string; name?: string }) =>
    api.put<Lot>(`/lots/${id}/debtor`, payload),
};

export const statsApi = {
  get: () => api.get<DashboardStats>("/stats"),
};

export const feedbackApi = {
  create: async (payload: {
    lot_id: number;
    action: "watch" | "reject" | "bought";
    recovered_amount?: number;
    expense_amount?: number;
    outcome?: "in_progress" | "recovered" | "not_recovered";
    outcome_at?: string;
    note?: string;
  }) => {
    try {
      return await api.post<Feedback>("/feedback", payload);
    } catch (error) {
      if (
        axios.isAxiosError(error) &&
        error.response?.status === 401 &&
        typeof window !== "undefined"
      ) {
        const key = window.prompt("Введите API-ключ AR Radar");
        if (key?.trim()) {
          window.sessionStorage.setItem("ar_radar_api_key", key.trim());
          return await api.post<Feedback>("/feedback", payload);
        }
      }
      throw error;
    }
  },
  list: async (lotId: number) => {
    try {
      return await api.get<Feedback[]>(`/lots/${lotId}/feedback`);
    } catch (error) {
      if (
        axios.isAxiosError(error) &&
        error.response?.status === 401 &&
        typeof window !== "undefined"
      ) {
        const key = normalizeApiKey(window.prompt("Введите API-ключ AR Radar"));
        if (key) {
          window.sessionStorage.setItem(API_KEY_STORAGE_KEY, key);
          return await api.get<Feedback[]>(`/lots/${lotId}/feedback`);
        }
      }
      throw error;
    }
  },
};
