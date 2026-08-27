/** API-клиент для бэкенда AR Radar. */
import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL || "/api/v1";

export const api = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
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

export interface DashboardStats {
  total_lots: number;
  receivable_lots: number;
  scored_lots: number;
  class_a: number;
  class_b: number;
  class_c: number;
  class_d: number;
  alerts_sent_today: number;
  last_ingest_at?: string;
}

export const lotsApi = {
  list: (params: Record<string, unknown> = {}) => api.get<LotList>("/lots", { params }),
  get: (id: number) => api.get<Lot>(`/lots/${id}`),
};

export const statsApi = {
  get: () => api.get<DashboardStats>("/stats"),
};
