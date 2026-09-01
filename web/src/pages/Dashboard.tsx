import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DashboardStats, statsApi } from "../api";
import { fmtDate } from "../utils";

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    statsApi
      .get()
      .then((r) => setStats(r.data))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="p-6 bg-red-50 border border-red-200 rounded text-red-800">
        ❌ Не удалось подключиться к API. Проверьте, что backend запущен на :8000.
        <br />
        <code className="text-xs">{error}</code>
      </div>
    );
  }

  if (!stats) {
    return <div className="p-6 text-slate-500">Загрузка…</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Дашборд</h1>
        <p className="text-sm text-slate-500 mt-1">
          Сводка по лотам публичного предложения с дебиторской задолженностью
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card title="Всего лотов" value={stats.total_lots} />
        <Card title="Ликвидная ДЗ" value={stats.receivable_lots} />
        <Card title="Можно участвовать" value={stats.active_lots} />
        <Card title="Исключено gate" value={stats.excluded_lots} />
        <Card title="Скоринг" value={stats.scored_lots} />
        <Card title="Устаревший скоринг" value={stats.stale_scored_lots} />
        <Card title="Готовые A/B" value={stats.ready_recommendations} />
        <Card title="Нужна проверка" value={stats.review_candidates} />
        <Card title="Алертов сегодня" value={stats.alerts_sent_today} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <ClassCard label="🟢 A" count={stats.class_a} />
        <ClassCard label="🟡 B" count={stats.class_b} />
        <ClassCard label="🟠 C" count={stats.class_c} />
        <ClassCard label="🔴 D" count={stats.class_d} />
      </div>

      <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
        <h3 className="font-semibold text-slate-900">Что делать дальше</h3>
        <ul className="mt-2 text-sm text-slate-700 space-y-1 list-disc list-inside">
          <li>Источник лотов: бесплатный публичный JSON ЦДТ; статус источника: <b>{stats.source_status}</b></li>
          <li>Запустите ingest: <code className="text-xs">python -m src.cli ingest</code></li>
          <li>Для полного цикла используйте <code className="text-xs">python -m src.cli prototype-run</code></li>
          <li>Документы: {stats.documents_completed} готово, {stats.documents_pending} в очереди, {stats.documents_needs_review} требуют проверки</li>
          <li>Строгие Telegram-рекомендации отправляются только для A/B без gaps; review-кандидаты проверяются вручную</li>
          <li>
            Перейдите в <Link to="/lots" className="text-blue-600 underline">ленту</Link>
          </li>
        </ul>
      </div>

      {stats.last_ingest_at && (
        <p className="text-xs text-slate-500">
          Последний ingest: {fmtDate(stats.last_ingest_at)}
        </p>
      )}
    </div>
  );
}

function Card({ title, value }: { title: string; value: number }) {
  return (
    <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{title}</div>
      <div className="text-2xl font-bold text-slate-900 mt-1">{value}</div>
    </div>
  );
}

function ClassCard({ label, count }: { label: string; count: number }) {
  return (
    <div className="bg-white p-4 rounded shadow-sm border border-slate-200">
      <div className="text-xs text-slate-500 uppercase tracking-wide">Класс {label}</div>
      <div className="text-2xl font-bold text-slate-900 mt-1">{count}</div>
    </div>
  );
}
