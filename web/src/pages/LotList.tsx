import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Lot, lotsApi } from "../api";
import { fmtMoney, fmtRelative } from "../utils";

type ScoreClass = "all" | "A" | "B" | "C" | "D";
type TriState = "all" | "yes" | "no";
type SortBy = "ev" | "price" | "deadline" | "updated";
type SortOrder = "asc" | "desc";

export default function LotList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [lots, setLots] = useState<Lot[]>([]);
  const [loading, setLoading] = useState(true);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const filter = asScoreClass(searchParams.get("score_class"));
  const search = searchParams.get("search") || "";
  const etpName = searchParams.get("etp_name") || "";
  const hasDebtor = asTriState(searchParams.get("has_debtor"));
  const hasCourt = asTriState(searchParams.get("has_court"));
  const deadlineBefore = searchParams.get("deadline_before") || "";
  const sortBy = asSortBy(searchParams.get("sort_by"));
  const sortOrder = asSortOrder(searchParams.get("sort_order"));
  const pageValue = Number(searchParams.get("page") || 1);
  const page = Number.isFinite(pageValue) && pageValue > 0 ? Math.floor(pageValue) : 1;

  const updateParam = (key: string, value: string, resetPage = true) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value);
      else next.delete(key);
      if (resetPage && key !== "page") next.delete("page");
      return next;
    }, { replace: true });
  };

  useEffect(() => {
    setLoading(true);
    setError(null);
    const params: Record<string, unknown> = { page, page_size: 20, price_status: "parsed", sort_by: sortBy, sort_order: sortOrder };
    if (filter !== "all") params.score_class = filter;
    if (search.trim()) params.search = search.trim();
    if (etpName.trim()) params.etp_name = etpName.trim();
    if (hasDebtor !== "all") params.has_debtor = hasDebtor === "yes";
    if (hasCourt !== "all") params.has_court = hasCourt === "yes";
    if (deadlineBefore) {
      const parsed = new Date(deadlineBefore);
      if (!Number.isNaN(parsed.valueOf())) params.deadline_before = parsed.toISOString();
    }
    lotsApi.list(params).then((response) => {
      setLots(response.data.items);
      setPages(response.data.pages);
      setTotal(response.data.total);
    }).catch((reason) => {
      setLots([]);
      setPages(1);
      setTotal(0);
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить лоты");
    }).finally(() => setLoading(false));
  }, [filter, page, search, etpName, hasDebtor, hasCourt, deadlineBefore, sortBy, sortOrder]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Лента лотов</h1>
        <div className="text-sm text-slate-500">{total} лот(ов)</div>
      </div>

      <div className="bg-white p-3 rounded shadow-sm border border-slate-200 flex gap-3 flex-wrap">
        <input aria-label="Поиск по лотам" type="text" placeholder="Поиск по ИНН / названию / описанию…" className="px-3 py-1.5 border border-slate-300 rounded text-sm flex-1 min-w-64" value={search} onChange={(event) => updateParam("search", event.target.value)} />
        <input aria-label="Фильтр по ЭТП" type="text" placeholder="ЭТП" className="px-3 py-1.5 border border-slate-300 rounded text-sm w-40" value={etpName} onChange={(event) => updateParam("etp_name", event.target.value)} />
        <select aria-label="Наличие дебитора" className="px-3 py-1.5 border border-slate-300 rounded text-sm" value={hasDebtor} onChange={(event) => updateParam("has_debtor", event.target.value === "all" ? "" : event.target.value === "yes" ? "true" : "false")}>
          <option value="all">Дебитор: любой</option><option value="yes">Дебитор найден</option><option value="no">Без дебитора</option>
        </select>
        <select aria-label="Наличие суда" className="px-3 py-1.5 border border-slate-300 rounded text-sm" value={hasCourt} onChange={(event) => updateParam("has_court", event.target.value === "all" ? "" : event.target.value === "yes" ? "true" : "false")}>
          <option value="all">Суд: любой</option><option value="yes">Есть суд</option><option value="no">Без суда</option>
        </select>
        <div className="flex gap-1" role="group" aria-label="Класс скоринга">
          {(["all", "A", "B", "C", "D"] as const).map((value) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => updateParam("score_class", value === "all" ? "" : value)} className={`px-3 py-1.5 text-sm rounded ${filter === value ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}>{value === "all" ? "Все" : `Класс ${value}`}</button>)}
        </div>
        <label className="text-sm text-slate-600 flex items-center gap-2">До дедлайна
          <input aria-label="Дедлайн до" type="datetime-local" className="px-2 py-1.5 border border-slate-300 rounded text-sm" value={deadlineBefore} onChange={(event) => updateParam("deadline_before", event.target.value)} />
        </label>
        <label className="text-sm text-slate-600 flex items-center gap-2">Сортировка
          <select aria-label="Сортировка лотов" className="px-2 py-1.5 border border-slate-300 rounded text-sm" value={`${sortBy}:${sortOrder}`} onChange={(event) => {
            const [nextBy, nextOrder] = event.target.value.split(":");
            setSearchParams((previous) => { const next = new URLSearchParams(previous); next.set("sort_by", nextBy); next.set("sort_order", nextOrder); next.delete("page"); return next; }, { replace: true });
          }}>
            <option value="ev:desc">EV: сначала выше</option><option value="ev:asc">EV: сначала ниже</option><option value="price:asc">Цена: сначала ниже</option><option value="price:desc">Цена: сначала выше</option><option value="deadline:asc">Дедлайн: ближайшие</option><option value="updated:desc">Обновление: новые</option>
          </select>
        </label>
        <button type="button" className="px-3 py-1.5 text-sm rounded bg-slate-100 text-slate-700 hover:bg-slate-200" onClick={() => setSearchParams({}, { replace: true })}>Сбросить</button>
      </div>

      {error ? <div role="alert" className="p-6 bg-red-50 border border-red-200 rounded text-red-800">Не удалось загрузить лоты: {error}</div> : loading ? <div className="p-6 text-slate-500" role="status">Загрузка…</div> : lots.length === 0 ? <EmptyState /> : <div className="space-y-2">
        {lots.map((lot) => <LotRow key={lot.id} lot={lot} />)}
        <div className="flex items-center justify-between pt-2 text-sm text-slate-600">
          <button type="button" disabled={page <= 1} onClick={() => updateParam("page", String(Math.max(1, page - 1)), false)} className="px-3 py-1.5 rounded border border-slate-300 disabled:opacity-40">← Назад</button>
          <span>Страница {page} из {pages}</span>
          <button type="button" disabled={page >= pages} onClick={() => updateParam("page", String(Math.min(pages, page + 1)), false)} className="px-3 py-1.5 rounded border border-slate-300 disabled:opacity-40">Далее →</button>
        </div>
      </div>}
    </div>
  );
}

function LotRow({ lot }: { lot: Lot }) {
  const cls = lot.score_class || "D";
  const debtor = lot.claims?.[0]?.debtor_party;
  const ev = lot.score_ev ? parseFloat(lot.score_ev) : 0;
  const evDisplay = ev < 0 ? "минус" : fmtMoney(lot.score_ev);
  return <Link to={`/lots/${lot.id}`} className="block bg-white p-4 rounded shadow-sm border border-slate-200 hover:shadow-md transition-shadow"><div className="flex items-center gap-4"><span className={`badge badge-${cls} text-base`}>{cls}</span><div className="flex-1 min-w-0"><div className="font-medium text-slate-900 truncate">{lot.title || `Лот №${lot.lot_no}`}</div><div className="text-xs text-slate-500 mt-0.5">{debtor ? <>Дебитор: {debtor.name || "—"} · ИНН {debtor.inn || "—"}</> : <span className="text-orange-600">⚠️ ИНН дебитора не извлечён</span>}</div><div className="text-xs text-slate-500 mt-1">Состояние данных: {dataStateLabel(lot.data_state)}</div></div><div className="text-right"><div className="text-lg font-bold text-slate-900">{evDisplay}</div><div className="text-xs text-slate-500">EV</div></div><div className="text-right"><div className="text-sm font-medium text-slate-700">{fmtMoney(lot.current_price)}</div><div className="text-xs text-slate-500">текущая цена</div></div><div className="text-right w-32"><div className="text-sm font-medium text-slate-700">{fmtRelative(lot.current_interval_to)}</div><div className="text-xs text-slate-500">до конца шага</div></div></div></Link>;
}

function EmptyState() {
  return <div className="bg-white p-8 rounded shadow-sm border border-slate-200 text-center"><div className="text-4xl mb-2">📭</div><div className="text-slate-700 font-medium">Нет лотов</div><div className="text-sm text-slate-500 mt-1">Измените фильтры или дождитесь следующего запуска ingest.</div></div>;
}

function asScoreClass(value: string | null): ScoreClass { return value && ["A", "B", "C", "D"].includes(value) ? value as Exclude<ScoreClass, "all"> : "all"; }
function asTriState(value: string | null): TriState { return value === "true" ? "yes" : value === "false" ? "no" : "all"; }
function asSortBy(value: string | null): SortBy { return value && ["ev", "price", "deadline", "updated"].includes(value) ? value as SortBy : "ev"; }
function asSortOrder(value: string | null): SortOrder { return value === "asc" ? "asc" : "desc"; }
function dataStateLabel(value: Lot["data_state"]): string { return value === "ready" ? "готово" : value === "needs_review" ? "нужна проверка" : value === "blocked" ? "заблокировано" : value === "stale" ? "устарело" : value === "unscored" ? "не оценено" : "неизвестно"; }
