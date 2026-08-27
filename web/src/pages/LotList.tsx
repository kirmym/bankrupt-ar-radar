import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Lot, lotsApi } from "../api";
import { fmtMoney, fmtRelative } from "../utils";

export default function LotList() {
  const [lots, setLots] = useState<Lot[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "A" | "B" | "C" | "D">("all");
  const [search, setSearch] = useState("");

  useEffect(() => {
    setLoading(true);
    lotsApi
      .list({ page: 1, page_size: 100 })
      .then((r) => setLots(r.data.items))
      .catch(() => setLots([]))
      .finally(() => setLoading(false));
  }, []);

  const filtered = lots.filter((lot) => {
    if (filter !== "all" && lot.score_class !== filter) return false;
    if (search) {
      const s = search.toLowerCase();
      const titleMatch = (lot.title || "").toLowerCase().includes(s);
      const innMatch = (lot.claims?.[0]?.debtor_party?.inn || "").includes(s);
      const nameMatch = (lot.claims?.[0]?.debtor_party?.name || "")
        .toLowerCase()
        .includes(s);
      if (!titleMatch && !innMatch && !nameMatch) return false;
    }
    return true;
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Лента лотов</h1>
        <div className="text-sm text-slate-500">
          {filtered.length} лот(ов)
        </div>
      </div>

      <div className="bg-white p-3 rounded shadow-sm border border-slate-200 flex gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Поиск по ИНН / названию / описанию…"
          className="px-3 py-1.5 border border-slate-300 rounded text-sm flex-1 min-w-64"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex gap-1">
          {(["all", "A", "B", "C", "D"] as const).map((c) => (
            <button
              key={c}
              onClick={() => setFilter(c)}
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

      {loading ? (
        <div className="p-6 text-slate-500">Загрузка…</div>
      ) : filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-2">
          {filtered.map((lot) => (
            <LotRow key={lot.id} lot={lot} />
          ))}
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
