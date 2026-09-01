import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { feedbackApi, Feedback, Lot, lotsApi } from "../api";
import { fmtDate, fmtMoney, fmtRelative } from "../utils";

function triState(value: boolean | null | undefined): {
  label: string;
  className: string;
} {
  if (value === true) return { label: "да", className: "text-red-600 font-medium" };
  if (value === false) return { label: "нет", className: "text-emerald-600" };
  return { label: "не проверено", className: "text-amber-600" };
}

export default function LotDetail() {
  const { id } = useParams<{ id: string }>();
  const [lot, setLot] = useState<Lot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback[]>([]);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [feedbackNote, setFeedbackNote] = useState("");
  const [debtorInn, setDebtorInn] = useState("");
  const [debtorName, setDebtorName] = useState("");
  const [savingDebtor, setSavingDebtor] = useState(false);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    lotsApi
      .get(parseInt(id, 10))
      .then((r) => setLot(r.data))
      .catch(() => {
        setLot(null);
        setError("Не удалось загрузить карточку лота");
      })
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    setFeedbackError(null);
    feedbackApi
      .list(parseInt(id, 10))
      .then((response) => setFeedback(response.data))
      .catch(() => setFeedbackError("История действий временно недоступна"));
  }, [id]);

  if (loading) return <div className="p-6 text-slate-500">Загрузка…</div>;
  if (!lot) return <div className="p-6 text-red-500" role="alert">{error || "Лот не найден"}</div>;

  const cls = lot.score_class || "D";
  const claims = lot.claims ?? [];
  const currentDebtor = claims[0]?.debtor_party;
  const dataState = dataStateLabel(lot.data_state);
  const refreshedAt = lot.price_observed_at || lot.updated_at;

  const reloadFeedback = () => {
    if (!id) return;
    feedbackApi.list(parseInt(id, 10)).then((response) => setFeedback(response.data)).catch(() => setFeedbackError("История действий временно недоступна"));
  };

  const saveDebtor = async () => {
    if (!id || !/^\d{10}(\d{2})?$/.test(debtorInn.trim())) {
      alert("Введите ИНН из 10 или 12 цифр");
      return;
    }
    setSavingDebtor(true);
    try {
      const response = await lotsApi.assignDebtor(parseInt(id, 10), {
        inn: debtorInn.trim(),
        name: debtorName.trim() || undefined,
      });
      setLot(response.data);
      setDebtorInn("");
      setDebtorName("");
    } catch {
      alert("Не удалось сохранить дебитора");
    } finally {
      setSavingDebtor(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-4">
        <span className={`badge badge-${cls} text-lg px-3 py-1`}>Класс {cls}</span>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-slate-900">
            {lot.title || `Лот №${lot.lot_no}`}
          </h1>
          {lot.description_text && (
            <p className="text-sm text-slate-600 mt-2 max-w-5xl whitespace-pre-wrap">{lot.description_text}</p>
          )}
          <div className="text-xs text-slate-500 mt-2">Состояние данных: <span className="font-medium text-slate-700">{dataState}</span> · обновлено {fmtDate(refreshedAt)}</div>
        </div>
        <div className="text-right">
          <div className="text-3xl font-bold text-slate-900">
            {fmtMoney(lot.score_ev)}
          </div>
          <div className="text-sm text-slate-500">EV (оптимист.)</div>
          <div className="text-xs text-slate-500 mt-1">
            коридор {fmtMoney(lot.score_ev_low)} — {fmtMoney(lot.score_ev_high)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Stat title="Текущая цена" value={fmtMoney(lot.current_price)} />
        <Stat title="Стартовая цена" value={fmtMoney(lot.start_price)} />
        <Stat title="Номинал" value={fmtMoney(lot.nominal_claimed)} />
        <Stat title="Max bid" value={fmtMoney(lot.score_max_bid)} />
        <Stat title="Сценарий" value={lot.score_scenario || "—"} />
        <Stat title="Статус графика цены" value={lot.price_schedule_status || "unknown"} />
        <Stat
          title="До конца интервала"
          value={`${fmtRelative(lot.current_interval_to)} (${fmtDate(lot.current_interval_to)})`}
        />
      </div>

      {lot.price_schedule_status === "unparsed" && (
        <div className="bg-amber-50 border border-amber-200 rounded p-4 text-sm text-amber-900">
          График снижения цены не распознан. Лот исключён из автоматических алертов до
          подтверждения актуальной цены.
        </div>
      )}

      {lot.score_gaps?.length > 0 && (
        <section className="bg-amber-50 border border-amber-200 rounded p-4" aria-label="Пробелы данных">
          <h3 className="font-semibold text-amber-900">Нужна проверка данных</h3>
          <ul className="mt-2 text-sm text-amber-800 list-disc list-inside">{lot.score_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>
        </section>
      )}

      <section className="bg-white p-4 rounded shadow-sm border border-slate-200" aria-label="Источники лота">
        <h2 className="font-semibold text-slate-900">Источники и проверка</h2>
        {lot.trade?.source_refs?.length ? (
          <ul className="mt-2 space-y-1 text-sm">
            {lot.trade.source_refs.map((ref) => (
              <li key={`${ref.source}:${ref.source_url}`}>
                <a href={ref.source_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                  {ref.source}
                </a>
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-sm text-slate-500">Ссылки на первоисточники не получены.</p>}
      </section>

      {claims.map((claim, index) => (
        <section key={claim.id} className="space-y-4">
          {claims.length > 1 && (
            <h2 className="text-lg font-semibold text-slate-800">
              Требование {index + 1} из {claims.length}
            </h2>
          )}
          {lot.trade?.source_refs?.length ? (
            <div className="text-xs text-slate-500 mt-2">
              Источники: {lot.trade.source_refs.map((ref) => (
                <a
                  key={`${ref.source}:${ref.source_url}`}
                  href={ref.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline mr-2"
                >
                  {ref.source}
                </a>
              ))}
            </div>
          ) : null}
          {claim.debtor_party && <DebtorCard debtor={claim.debtor_party} />}
          <ClaimCard claim={claim} />
        </section>
      ))}

      <section className="bg-white p-4 rounded shadow-sm border border-slate-200">
        <h3 className="font-semibold text-slate-900">Дебитор не найден автоматически?</h3>
        <p className="text-sm text-slate-500 mt-1">
          Укажите ИНН вручную. Это пометит лот для обогащения и нового скоринга.
          {currentDebtor?.inn ? ` Сейчас: ${currentDebtor.inn}.` : ""}
        </p>
        <div className="mt-3 flex gap-2 flex-wrap">
          <input
            className="px-3 py-1.5 border border-slate-300 rounded text-sm"
            placeholder="ИНН"
            inputMode="numeric"
            value={debtorInn}
            onChange={(event) => setDebtorInn(event.target.value.replace(/\D/g, "").slice(0, 12))}
          />
          <input
            className="px-3 py-1.5 border border-slate-300 rounded text-sm flex-1 min-w-56"
            placeholder="Название (необязательно)"
            value={debtorName}
            onChange={(event) => setDebtorName(event.target.value)}
          />
          <button
            type="button"
            disabled={savingDebtor}
            onClick={saveDebtor}
            className="px-3 py-1.5 bg-slate-900 text-white rounded text-sm disabled:opacity-50"
          >
            {savingDebtor ? "Сохранение…" : "Сохранить ИНН"}
          </button>
        </div>
      </section>

      {lot.score_stop_factors?.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded p-4">
          <h3 className="font-semibold text-red-800">⚠️ Стоп-факторы</h3>
          <ul className="mt-2 text-sm text-red-700 list-disc list-inside">
            {lot.score_stop_factors.map((sf) => (
              <li key={sf}>{sf}</li>
            ))}
          </ul>
        </div>
      )}

      {lot.price_intervals?.length > 0 && (
        <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
          <h3 className="font-semibold text-slate-900">График цены</h3>
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500 border-b border-slate-200">
                <th className="pb-2">№</th>
                <th className="pb-2">Цена</th>
                <th className="pb-2">Начало</th>
                <th className="pb-2">Конец</th>
              </tr>
            </thead>
            <tbody>
              {lot.price_intervals.map((pi) => (
                <tr key={pi.seq} className={pi.is_current ? "bg-yellow-50" : ""}>
                  <td className="py-1">{pi.seq}</td>
                  <td className="py-1">{fmtMoney(pi.price)}</td>
                  <td className="py-1">{fmtDate(pi.starts_at)}</td>
                  <td className="py-1">{fmtDate(pi.ends_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section className="bg-white p-4 rounded shadow-sm border border-slate-200">
        <h3 className="font-semibold text-slate-900">Документы</h3>
        {lot.documents?.length ? <ul className="mt-2 space-y-2 text-sm">{lot.documents.map((document) => <li key={document.id} className="flex flex-wrap gap-2 items-center"><span>{document.title || document.kind || `Документ ${document.id}`}</span><span className="text-xs text-slate-500">({document.processing_status})</span>{document.url && <a className="text-blue-600 hover:underline" href={document.url} target="_blank" rel="noreferrer">Открыть источник</a>}{document.last_error && <span className="text-red-600">{document.last_error}</span>}</li>)}</ul> : <p className="mt-2 text-sm text-slate-500">Документы не обнаружены.</p>}
      </section>

      <section className="bg-white p-4 rounded shadow-sm border border-slate-200">
        <h3 className="font-semibold text-slate-900">История скоринга</h3>
        {lot.score_snapshots?.length ? <div className="overflow-x-auto"><table className="mt-3 w-full text-sm"><thead><tr className="text-left text-slate-500 border-b border-slate-200"><th className="pb-2">Дата</th><th className="pb-2">Версия</th><th className="pb-2">Класс</th><th className="pb-2">EV</th><th className="pb-2">Gaps</th></tr></thead><tbody>{lot.score_snapshots.map((snapshot) => <tr key={snapshot.id} className="border-b border-slate-100"><td className="py-1">{fmtDate(snapshot.scored_at)}</td><td className="py-1">{snapshot.model_version}</td><td className="py-1">{snapshot.score_class}</td><td className="py-1">{fmtMoney(snapshot.ev)}</td><td className="py-1">{snapshot.gaps?.join(", ") || "—"}</td></tr>)}</tbody></table></div> : <p className="mt-2 text-sm text-slate-500">История появится после первого пересчёта.</p>}
      </section>

      <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
        <h3 className="font-semibold text-slate-900">Действие</h3>
        <div className="mt-3 flex gap-2 flex-wrap">
          <FeedbackButton lotId={lot.id} action="watch" label="👁 В работу" note={feedbackNote} onSaved={reloadFeedback} />
          <FeedbackButton lotId={lot.id} action="reject" label="🚫 Отказ" note={feedbackNote} onSaved={reloadFeedback} />
          <FeedbackButton lotId={lot.id} action="bought" label="✅ Куплено" note={feedbackNote} onSaved={reloadFeedback} />
        </div>
        <textarea
          className="mt-3 w-full border border-slate-300 rounded p-2 text-sm"
          rows={2}
          placeholder="Заметка (необязательно)…"
          value={feedbackNote}
          onChange={(e) => setFeedbackNote(e.target.value)}
        />
        <OutcomeForm lotId={lot.id} note={feedbackNote} onSaved={reloadFeedback} />
        {feedbackError ? <p role="alert" className="mt-3 text-sm text-red-700">{feedbackError}</p> : feedback.length ? <div className="mt-4 border-t border-slate-200 pt-3"><h4 className="font-medium text-slate-800">История действий</h4><ul className="mt-2 space-y-2 text-sm">{feedback.map((entry) => <li key={entry.id} className="rounded bg-slate-50 p-2"><span className="font-medium">{feedbackActionLabel(entry.action)}</span>{entry.outcome && <> · исход: {feedbackOutcomeLabel(entry.outcome)}</>}{entry.recovered_amount && <> · взыскано {fmtMoney(entry.recovered_amount)}</>}{entry.expense_amount && <> · расходы {fmtMoney(entry.expense_amount)}</>}<span className="text-xs text-slate-500"> · {fmtDate(entry.outcome_at || entry.created_at)}</span>{entry.note && <div className="text-slate-600 mt-1">{entry.note}</div>}</li>)}</ul></div> : <p className="mt-3 text-sm text-slate-500">Действий пока нет.</p>}
      </div>
    </div>
  );
}

function Stat({ title, value }: { title: string; value: string }) {
  return (
    <div className="bg-white p-3 rounded shadow-sm border border-slate-200">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{title}</div>
      <div className="text-lg font-medium text-slate-900 mt-1">{value}</div>
    </div>
  );
}

function DebtorCard({ debtor }: { debtor: NonNullable<Lot["claims"][0]["debtor_party"]> }) {
  const kad = triState(debtor.kad_bankruptcy_open);
  const fssp = triState(debtor.fssp_uncollectible);
  return (
    <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
      <h3 className="font-semibold text-slate-900">Дебитор</h3>
      <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-slate-500">Наименование:</span>{" "}
          <span className="text-slate-900">{debtor.name || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">ИНН:</span>{" "}
          <span className="text-slate-900 font-mono">{debtor.inn || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Статус ЕГРЮЛ:</span>{" "}
          <span
            className={
              debtor.status && debtor.status !== "active"
                ? "text-red-600"
                : "text-slate-900"
            }
          >
            {debtor.status || "—"}
          </span>
        </div>
        <div>
          <span className="text-slate-500">Директор:</span>{" "}
          <span className="text-slate-900">{debtor.director_name || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Выручка:</span>{" "}
          <span className="text-slate-900">{fmtMoney(debtor.revenue)}</span>
        </div>
        <div>
          <span className="text-slate-500">Деньги:</span>{" "}
          <span className="text-slate-900">{fmtMoney(debtor.cash)}</span>
        </div>
        <div>
          <span className="text-slate-500">Чистые активы:</span>{" "}
          <span className="text-slate-900">{fmtMoney(debtor.equity)}</span>
        </div>
        <div>
          <span className="text-slate-500">ИП ФССП на сумму:</span>{" "}
          <span className="text-slate-900">{fmtMoney(debtor.fssp_sum)}</span>
        </div>
        <div>
          <span className="text-slate-500">Дел в КАД как ответчик:</span>{" "}
          <span className="text-slate-900">{debtor.kad_as_defendant_count ?? "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Банкротство дебитора:</span>{" "}
          <span className={kad.className}>{kad.label}</span>
        </div>
        <div>
          <span className="text-slate-500">Безнадёжные ИП ФССП:</span>{" "}
          <span className={fssp.className}>{fssp.label}</span>
        </div>
      </div>
      {debtor.source_checks?.length ? (
        <div className="mt-3 text-xs text-slate-500">
          Проверки источников:{" "}
          {debtor.source_checks.map((check) => (
            <span key={check.source} className="mr-2">
              {check.source}: {check.status === "success" ? "успешно" : check.status}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ClaimCard({ claim }: { claim: Lot["claims"][0] }) {
  const judgment = triState(claim.has_judgment);
  const writ = triState(claim.has_writ);
  return (
    <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
      <h3 className="font-semibold text-slate-900">Требование</h3>
      <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-slate-500">Вид:</span>{" "}
          <span className="text-slate-900">{claim.kind}</span>
        </div>
        <div>
          <span className="text-slate-500">Тело:</span>{" "}
          <span className="text-slate-900 font-medium">{fmtMoney(claim.principal)}</span>
        </div>
        <div>
          <span className="text-slate-500">Пени:</span>{" "}
          <span className="text-slate-900">{fmtMoney(claim.penalties)}</span>
        </div>
        <div>
          <span className="text-slate-500">Договор:</span>{" "}
          <span className="text-slate-900">{claim.base_contract || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Дата возникновения:</span>{" "}
          <span className="text-slate-900">{claim.base_date || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Срок давности:</span>{" "}
          <span className="text-slate-900">{claim.limitations_deadline || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Дело о взыскании:</span>{" "}
          <span className="text-slate-900">{claim.court_case_no || "—"}</span>
        </div>
        <div>
          <span className="text-slate-500">Решение суда:</span>{" "}
          <span className={judgment.className}>{judgment.label}</span>
        </div>
        <div>
          <span className="text-slate-500">Исполнительный лист:</span>{" "}
          <span className={writ.className}>{writ.label}</span>
        </div>
        <div>
          <span className="text-slate-500">Обеспечение:</span>{" "}
          <span className={claim.secured ? "text-emerald-600" : "text-slate-500"}>
            {claim.secured ? "✅" : "нет"}
          </span>
        </div>
      </div>
    </div>
  );
}

function FeedbackButton({
  lotId,
  action,
  label,
  note,
  onSaved,
}: {
  lotId: number;
  action: "watch" | "reject" | "bought";
  label: string;
  note: string;
  onSaved: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const onClick = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await feedbackApi.create({ lot_id: lotId, action, note: note.trim() || undefined });
      onSaved();
    } catch {
      alert("Не удалось сохранить действие. Проверьте доступ к API.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <button
      type="button"
      disabled={saving}
      onClick={onClick}
      className="px-3 py-1.5 bg-slate-900 text-white rounded text-sm hover:bg-slate-700"
    >
      {saving ? "Сохранение…" : label}
    </button>
  );
}

function OutcomeForm({ lotId, note, onSaved }: { lotId: number; note: string; onSaved: () => void }) {
  const [outcome, setOutcome] = useState<"" | "in_progress" | "recovered" | "not_recovered">("");
  const [recoveredAmount, setRecoveredAmount] = useState("");
  const [expenseAmount, setExpenseAmount] = useState("");
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!outcome || saving) return;
    setSaving(true);
    try {
      await feedbackApi.create({ lot_id: lotId, action: "bought", outcome, recovered_amount: recoveredAmount ? Number(recoveredAmount) : undefined, expense_amount: expenseAmount ? Number(expenseAmount) : undefined, note: note.trim() || undefined });
      onSaved();
      setOutcome("");
      setRecoveredAmount("");
      setExpenseAmount("");
    } catch {
      alert("Не удалось сохранить исход взыскания");
    } finally {
      setSaving(false);
    }
  };
  return <div className="mt-4 border-t border-slate-200 pt-3"><h4 className="font-medium text-slate-800">Исход взыскания</h4><div className="mt-2 flex gap-2 flex-wrap"><select aria-label="Исход взыскания" className="px-3 py-1.5 border border-slate-300 rounded text-sm" value={outcome} onChange={(event) => setOutcome(event.target.value as typeof outcome)}><option value="">Выберите исход</option><option value="in_progress">В процессе</option><option value="recovered">Взыскано</option><option value="not_recovered">Не взыскано</option></select><input aria-label="Сумма взыскания" type="number" min="0" step="0.01" placeholder="Сумма взыскания" className="px-3 py-1.5 border border-slate-300 rounded text-sm" value={recoveredAmount} onChange={(event) => setRecoveredAmount(event.target.value)} /><input aria-label="Расходы по взысканию" type="number" min="0" step="0.01" placeholder="Расходы" className="px-3 py-1.5 border border-slate-300 rounded text-sm" value={expenseAmount} onChange={(event) => setExpenseAmount(event.target.value)} /><button type="button" disabled={!outcome || saving} onClick={save} className="px-3 py-1.5 bg-emerald-700 text-white rounded text-sm disabled:opacity-50">{saving ? "Сохранение…" : "Сохранить исход"}</button></div></div>;
}

function dataStateLabel(value: Lot["data_state"]): string { return value === "ready" ? "готово" : value === "needs_review" ? "нужна проверка" : value === "blocked" ? "заблокировано" : value === "stale" ? "устарело" : value === "unscored" ? "не оценено" : "неизвестно"; }
function feedbackActionLabel(value: string): string { return value === "watch" ? "В работе" : value === "reject" ? "Отклонено" : "Куплено"; }
function feedbackOutcomeLabel(value: string): string { return value === "in_progress" ? "в процессе" : value === "recovered" ? "взыскано" : "не взыскано"; }
