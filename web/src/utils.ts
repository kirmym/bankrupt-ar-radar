export function fmtMoney(value?: string | number | null, currency = "₽"): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (Number.isNaN(num)) return "—";
  return new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 0,
  }).format(num) + ` ${currency}`;
}

export function fmtDate(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  const now = new Date();
  const diff = (d.getTime() - now.getTime()) / 1000;
  if (diff < 0) return "просрочено";
  if (diff < 60) return "менее минуты";
  if (diff < 3600) return `${Math.round(diff / 60)} мин`;
  if (diff < 86400) return `${Math.round(diff / 3600)} ч`;
  return `${Math.round(diff / 86400)} дн`;
}
