/** API-клиент для бэкенда AR Radar. */
import axios from "axios";

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
    const key = window.sessionStorage.getItem("ar_radar_api_key");
    if (key) {
      config.headers = config.headers ?? {};
      config.headers["X-API-Key"] = key;
    }
  }
  return config;
});

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
  kad_bankruptcy_open?: boolean;
  fssp_sum?: string;
  fssp_uncollectible?: boolean;
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
  has_judgment: boolean;
  has_writ: boolean;
  enforcement_alive: boolean;
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
  price_intervals: PriceInterval[];
  claims: Claim[];
  created_at: string;
  updated_at: string;
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
};
