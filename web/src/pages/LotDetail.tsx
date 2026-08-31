import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { feedbackApi, Lot, lotsApi } from "../api";
import { fmtDate, fmtMoney, fmtRelative } from "../utils";

export default function LotDetail() {
  const { id } = useParams<{ id: string }>();
  const [lot, setLot] = useState<Lot | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedbackNote, setFeedbackNote] = useState("");
  const [debtorInn, setDebtorInn] = useState("");
  const [debtorName, setDebtorName] = useState("");
  const [savingDebtor, setSavingDebtor] = useState(false);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    lotsApi
      .get(parseInt(id, 10))
      .then((r) => setLot(r.data))
      .catch(() => setLot(null))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="p-6 text-slate-500">Загрузка…</div>;
  if (!lot) return <div className="p-6 text-red-500">Лот не найден</div>;

  const cls = lot.score_class || "D";
  const claims = lot.claims ?? [];
  const currentDebtor = claims[0]?.debtor_party;

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
            <p className="text-sm text-slate-600 mt-2 max-w-3xl">
              {lot.description_text.slice(0, 500)}
              {lot.description_text.length > 500 && "…"}
            </p>
          )}
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

      {claims.map((claim, index) => (
        <section key={claim.id} className="space-y-4">
          {claims.length > 1 && (
            <h2 className="text-lg font-semibold text-slate-800">
              Требование {index + 1} из {claims.length}
            </h2>
          )}
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

      <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
        <h3 className="font-semibold text-slate-900">Действие</h3>
        <div className="mt-3 flex gap-2 flex-wrap">
          <FeedbackButton lotId={lot.id} action="watch" label="👁 В работу" note={feedbackNote} />
          <FeedbackButton lotId={lot.id} action="reject" label="🚫 Отказ" note={feedbackNote} />
          <FeedbackButton lotId={lot.id} action="bought" label="✅ Куплено" note={feedbackNote} />
        </div>
        <textarea
          className="mt-3 w-full border border-slate-300 rounded p-2 text-sm"
          rows={2}
          placeholder="Заметка (необязательно)…"
          value={feedbackNote}
          onChange={(e) => setFeedbackNote(e.target.value)}
        />
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
          <span
            className={
              debtor.kad_bankruptcy_open ? "text-red-600 font-medium" : "text-slate-900"
            }
          >
            {debtor.kad_bankruptcy_open ? "да" : "нет"}
          </span>
        </div>
      </div>
    </div>
  );
}

function ClaimCard({ claim }: { claim: Lot["claims"][0] }) {
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
          <span className={claim.has_judgment ? "text-emerald-600" : "text-slate-500"}>
            {claim.has_judgment ? "✅ есть" : "нет"}
          </span>
        </div>
        <div>
          <span className="text-slate-500">Исполнительный лист:</span>{" "}
          <span className={claim.has_writ ? "text-emerald-600" : "text-slate-500"}>
            {claim.has_writ ? "✅ есть" : "нет"}
          </span>
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
}: {
  lotId: number;
  action: "watch" | "reject" | "bought";
  label: string;
  note: string;
}) {
  const onClick = async () => {
    try {
      await feedbackApi.create({ lot_id: lotId, action, note: note.trim() || undefined });
      alert(`Сохранено: ${action}`);
    } catch {
      alert("Не удалось сохранить действие. Проверьте доступ к API.");
    }
  };
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 bg-slate-900 text-white rounded text-sm hover:bg-slate-700"
    >
      {label}
    </button>
  );
}
