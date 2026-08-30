import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Lot, lotsApi } from "../api";
import { fmtMoney, fmtRelative } from "../utils";

export default function LotList() {
  const [lots, setLots] = useState<Lot[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "A" | "B" | "C" | "D">("all");
  const [search, setSearch] = useState("");
  const [etpName, setEtpName] = useState("");
  const [hasDebtor, setHasDebtor] = useState<"all" | "yes" | "no">("all");
  const [hasCourt, setHasCourt] = useState<"all" | "yes" | "no">("all");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const params: Record<string, unknown> = { page, page_size: 20 };
    if (filter !== "all") params.score_class = filter;
    if (search.trim()) params.search = search.trim();
    if (etpName.trim()) params.etp_name = etpName.trim();
    if (hasDebtor !== "all") params.has_debtor = hasDebtor === "yes";
    if (hasCourt !== "all") params.has_court = hasCourt === "yes";
    params.price_status = "parsed";
    lotsApi
      .list(params)
      .then((r) => {
        setLots(r.data.items);
        setPages(r.data.pages);
        setTotal(r.data.total);
      })
      .catch((e) => {
        setLots([]);
        setPages(1);
        setTotal(0);
        setError(e instanceof Error ? e.message : "Не удалось загрузить лоты");
      })
      .finally(() => setLoading(false));
  }, [filter, page, search, etpName, hasDebtor, hasCourt]);

  const changeFilter = (next: typeof filter) => {
    setFilter(next);
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Лента лотов</h1>
        <div className="text-sm text-slate-500">
          {total} лот(ов)
        </div>
      </div>

      <div className="bg-white p-3 rounded shadow-sm border border-slate-200 flex gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Поиск по ИНН / названию / описанию…"
          className="px-3 py-1.5 border border-slate-300 rounded text-sm flex-1 min-w-64"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <input
          type="text"
          placeholder="ЭТП"
          className="px-3 py-1.5 border border-slate-300 rounded text-sm w-40"
          value={etpName}
          onChange={(e) => {
            setEtpName(e.target.value);
            setPage(1);
          }}
        />
        <select
          className="px-3 py-1.5 border border-slate-300 rounded text-sm"
          value={hasDebtor}
          onChange={(e) => {
            setHasDebtor(e.target.value as typeof hasDebtor);
            setPage(1);
          }}
        >
          <option value="all">Дебитор: любой</option>
          <option value="yes">Дебитор найден</option>
          <option value="no">Без дебитора</option>
        </select>
        <select
          className="px-3 py-1.5 border border-slate-300 rounded text-sm"
          value={hasCourt}
          onChange={(e) => {
            setHasCourt(e.target.value as typeof hasCourt);
            setPage(1);
          }}
        >
          <option value="all">Суд: любой</option>
          <option value="yes">Есть суд</option>
          <option value="no">Без суда</option>
        </select>
        <div className="flex gap-1">
          {(["all", "A", "B", "C", "D"] as const).map((c) => (
            <button
              key={c}
              onClick={() => changeFilter(c)}
              className={`px-3 py-1.5 text-sm rounded ${
                filter === c
                  ? "bg-slate-900 text-white"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
            >
              {c === "all" ? "Все" : `Класс ${c}`}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <div className="p-6 bg-red-50 border border-red-200 rounded text-red-800">
          Не удалось загрузить лоты: {error}
        </div>
      ) : loading ? (
        <div className="p-6 text-slate-500">Загрузка…</div>
      ) : lots.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-2">
          {lots.map((lot) => (
            <LotRow key={lot.id} lot={lot} />
          ))}
          <div className="flex items-center justify-between pt-2 text-sm text-slate-600">
            <button
              disabled={page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              className="px-3 py-1.5 rounded border border-slate-300 disabled:opacity-40"
            >
              ← Назад
            </button>
            <span>Страница {page} из {pages}</span>
            <button
              disabled={page >= pages}
              onClick={() => setPage((current) => Math.min(pages, current + 1))}
              className="px-3 py-1.5 rounded border border-slate-300 disabled:opacity-40"
            >
              Далее →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function LotRow({ lot }: { lot: Lot }) {
  const cls = lot.score_class || "D";
  const debtor = lot.claims?.[0]?.debtor_party;
  const ev = lot.score_ev ? parseFloat(lot.score_ev) : 0;
  const evDisplay = ev < 0 ? "минус" : fmtMoney(lot.score_ev);

  return (
    <Link
      to={`/lots/${lot.id}`}
      className="block bg-white p-4 rounded shadow-sm border border-slate-200 hover:shadow-md transition-shadow"
    >
      <div className="flex items-center gap-4">
        <span className={`badge badge-${cls} text-base`}>{cls}</span>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-slate-900 truncate">
            {lot.title || `Лот №${lot.lot_no}`}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {debtor ? (
              <>
                Дебитор: {debtor.name || "—"} · ИНН {debtor.inn || "—"}
                {debtor.status && debtor.status !== "active" && (
                  <span className="text-red-600 ml-1">({debtor.status})</span>
                )}
              </>
            ) : (
              <span className="text-orange-600">⚠️ ИНН дебитора не извлечён</span>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-lg font-bold text-slate-900">{evDisplay}</div>
          <div className="text-xs text-slate-500">EV</div>
        </div>
        <div className="text-right">
          <div className="text-sm font-medium text-slate-700">
            {fmtMoney(lot.current_price)}
          </div>
          <div className="text-xs text-slate-500">текущая цена</div>
        </div>
        <div className="text-right w-32">
          <div className="text-sm font-medium text-slate-700">
            {fmtRelative(lot.current_interval_to)}
          </div>
          <div className="text-xs text-slate-500">до конца шага</div>
        </div>
      </div>
    </Link>
  );
}

function EmptyState() {
  return (
    <div className="bg-white p-8 rounded shadow-sm border border-slate-200 text-center">
      <div className="text-4xl mb-2">📭</div>
      <div className="text-slate-700 font-medium">Нет лотов</div>
      <div className="text-sm text-slate-500 mt-1">
        Запустите ingest ЕФРСБ: <code className="text-xs">python -m src.cli ingest</code>
      </div>
    </div>
  );
}
