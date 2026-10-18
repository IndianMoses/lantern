export function money(v: number | null | undefined, currency = "GBP", lang = "en"): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  try {
    return new Intl.NumberFormat(lang === "zh" ? "zh-CN" : "en-GB", { style: "currency", currency, maximumFractionDigits: v % 1 === 0 ? 0 : 2 }).format(v);
  } catch {
    return `${v.toLocaleString()} ${currency}`;
  }
}

export function fmtDate(iso: string | null | undefined, lang = "en"): string {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(lang === "zh" ? "zh-CN" : "en-GB", { year: "numeric", month: "short", day: "numeric" });
}

export function fmtDateTime(iso: string | null | undefined, lang = "en"): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(lang === "zh" ? "zh-CN" : "en-GB", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const STATUS_TONE: Record<string, string> = {
  draft: "", information_required: "warn", ready_for_review: "info", awaiting_customer_approval: "accent", submitted: "info", acknowledged: "info", under_review: "info",
  additional_information_requested: "warn", decision_issued: "info", approved: "ok", partially_approved: "ok", declined: "bad", settled: "ok", closed: "", reopened: "warn", submission_failed: "bad",
  active: "ok", expired: "", cancelled: "bad", pending_review: "warn", renewing_soon: "warn",
  pending: "accent", consumed: "ok", rejected: "bad", failed: "bad",
  gap: "bad", partial: "warn", duplicate: "purple", covered: "ok", note: "info",
  appears_covered: "ok", appears_covered_with_possible_exclusion: "warn", possible_exclusion: "warn", unclear: "warn", unclear_subject: "warn", not_addressed: "", no_policy: "",
};

export function humanize(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export const VERDICT_LABEL: Record<string, { en: string; zh: string }> = {
  appears_covered: { en: "Appears covered, subject to terms", zh: "看起来在保障范围内（以条款为准）" },
  appears_covered_with_possible_exclusion: { en: "Appears covered; an exclusion may apply", zh: "看起来有保障，但可能适用除外责任" },
  possible_exclusion: { en: "A possible exclusion applies", zh: "可能适用除外责任" },
  unclear_subject: { en: "Policy may not be the right one for this item", zh: "该保单可能不适用于所述物品" },
  unclear: { en: "Unclear from the wording", zh: "条款表述不明确" },
  not_addressed: { en: "Not addressed by the wording", zh: "条款未涉及" },
  no_policy: { en: "No policy linked", zh: "未关联保单" },
};
