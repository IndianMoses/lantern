import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type Lang = "en" | "zh";

const dict: Record<string, { en: string; zh: string }> = {
  // navigation
  overview: { en: "Overview", zh: "概览" },
  assistant: { en: "Assistant", zh: "助手" },
  my_insurance: { en: "My insurance", zh: "我的保险" },
  gaps: { en: "Coverage gaps", zh: "保障缺口" },
  claims: { en: "Claims", zh: "理赔" },
  documents: { en: "Documents", zh: "文件" },
  messages: { en: "Messages", zh: "消息" },
  settings: { en: "Settings", zh: "设置" },
  sign_out: { en: "Sign out", zh: "退出" },
  operations: { en: "Operations", zh: "运营概览" },
  queue: { en: "Claims queue", zh: "理赔队列" },
  customers: { en: "Customers", zh: "客户" },
  team: { en: "Team & audit", zh: "团队与审计" },
  // common
  loading: { en: "Loading…", zh: "加载中…" },
  save: { en: "Save", zh: "保存" },
  cancel: { en: "Cancel", zh: "取消" },
  send: { en: "Send", zh: "发送" },
  approve: { en: "Approve", zh: "批准" },
  reject: { en: "Reject", zh: "拒绝" },
  download: { en: "Download", zh: "下载" },
  upload: { en: "Upload", zh: "上传" },
  edit: { en: "Edit", zh: "编辑" },
  close: { en: "Close", zh: "关闭" },
  back: { en: "Back", zh: "返回" },
  next: { en: "Next", zh: "下一步" },
  view: { en: "View", zh: "查看" },
  status: { en: "Status", zh: "状态" },
  demo_note: { en: "Demonstration environment: all insurers, policies and claims are fictional.", zh: "演示环境：所有保险公司、保单和理赔均为虚构。" },
  last_known: { en: "Last known status", zh: "最近已知状态" },
  from: { en: "from", zh: "来源" },
  // overview
  active_policies: { en: "Active policies", zh: "有效保单" },
  annual_premium: { en: "Annual premium", zh: "年保费" },
  monthly_equiv: { en: "per month", zh: "每月" },
  renewals_60: { en: "Renewals in 60 days", zh: "60天内到期" },
  open_claims: { en: "Open claims", zh: "进行中理赔" },
  actions: { en: "Action centre", zh: "待办事项" },
  insights: { en: "Insights from your last gap analysis", zh: "上次缺口分析的发现" },
  no_actions: { en: "Nothing needs your attention right now.", zh: "目前没有需要处理的事项。" },
  run_gap_prompt: { en: "Run a coverage gap analysis to see insights here.", zh: "运行保障缺口分析后，这里会显示发现。" },
  recent_claims: { en: "Recent claims", zh: "最近理赔" },
  portfolio: { en: "Your portfolio", zh: "你的保单" },
  // policies
  policy_number: { en: "Policy number", zh: "保单号" },
  provider: { en: "Provider", zh: "保险公司" },
  period: { en: "Period", zh: "保险期间" },
  premium: { en: "Premium", zh: "保费" },
  excess: { en: "Excess", zh: "免赔额" },
  renews_in: { en: "Renews in", zh: "距到期" },
  days: { en: "days", zh: "天" },
  expired: { en: "Expired", zh: "已到期" },
  pending_review: { en: "Needs your confirmation", zh: "待你确认" },
  upload_policy: { en: "Upload a policy", zh: "上传保单" },
  clauses: { en: "Clauses", zh: "条款" },
  timeline: { en: "Timeline", zh: "时间线" },
  key_limits: { en: "Key limits", zh: "主要限额" },
  key_exclusions: { en: "Key exclusions", zh: "主要除外" },
  ask_about_policy: { en: "Ask the assistant about this policy", zh: "向助手询问此保单" },
  translate: { en: "Translate", zh: "翻译" },
  original: { en: "Original", zh: "原文" },
  confirm_activate: { en: "Confirm and activate", zh: "确认并激活" },
  // gaps
  run_analysis: { en: "Run analysis", zh: "开始分析" },
  rerun_analysis: { en: "Run again", zh: "重新分析" },
  gap: { en: "Gap", zh: "缺口" },
  partial: { en: "Partial", zh: "部分保障" },
  duplicate: { en: "Duplicate", zh: "重复" },
  covered: { en: "Covered", zh: "已保障" },
  note: { en: "Note", zh: "提示" },
  evidence: { en: "Evidence", zh: "依据" },
  suggested: { en: "Suggested", zh: "建议" },
  triggered_by: { en: "Based on", zh: "依据资料" },
  // claims
  new_claim: { en: "Report an incident", zh: "报告事件" },
  checklist: { en: "Checklist", zh: "材料清单" },
  completeness: { en: "Pack completeness", zh: "材料完整度" },
  coverage_reading: { en: "Coverage reading", zh: "保障解读" },
  submit_claim: { en: "Submit claim", zh: "提交理赔" },
  assisted_pack: { en: "Download claim pack (PDF)", zh: "下载理赔材料包（PDF）" },
  mark_sent: { en: "I sent it myself", zh: "我已自行发送" },
  information_requests: { en: "Requests from the insurer", zh: "保险公司的要求" },
  respond: { en: "Respond", zh: "回复" },
  decision: { en: "Decision", zh: "理赔结论" },
  explain_decision: { en: "Ask the assistant to explain this decision", zh: "让助手解释这个结论" },
  required: { en: "Required", zh: "必需" },
  recommended: { en: "Recommended", zh: "建议" },
  optional: { en: "Optional", zh: "可选" },
  why_needed: { en: "Why", zh: "原因" },
  how_to_get: { en: "How to obtain", zh: "如何取得" },
  incident: { en: "Incident", zh: "事件" },
  submissions: { en: "Submission record", zh: "提交记录" },
  mock_ack: { en: "mock acknowledgement", zh: "模拟确认" },
  // assistant
  new_conversation: { en: "New conversation", zh: "新对话" },
  ask_placeholder: { en: "Ask about your cover, report what happened, or ask for a draft…", zh: "询问保障、描述发生的事情，或让我起草信件…" },
  why_this_answer: { en: "Why this answer", zh: "回答依据" },
  tools_used: { en: "Tools used", zh: "调用的工具" },
  citations: { en: "Citations", zh: "引用条款" },
  approval_required: { en: "Your approval is required", zh: "需要你的批准" },
  nothing_sent: { en: "Nothing has been sent yet.", zh: "尚未发送任何内容。" },
  consequences: { en: "What happens if you approve", zh: "批准后会发生什么" },
  expires: { en: "Expires", zh: "过期时间" },
  attach: { en: "Attach a document", zh: "附加文件" },
  download_log: { en: "Download operation log", zh: "下载操作日志" },
  // messages
  inbox: { en: "Inbox", zh: "收件箱" },
  drafts: { en: "Drafts", zh: "草稿" },
  sent: { en: "Sent", zh: "已发送" },
  // settings
  profile: { en: "Your profile", zh: "你的资料" },
  assets: { en: "Things to protect", zh: "需要保障的资产" },
  activities: { en: "Activities and circumstances", zh: "活动与情况" },
  language: { en: "Language", zh: "语言" },
  export_data: { en: "Export my data", zh: "导出我的数据" },
  // insurer
  handler: { en: "Handler", zh: "理赔员" },
  unassigned: { en: "Unassigned", zh: "未分配" },
  assign_me: { en: "Assign to me", zh: "分配给我" },
  request_info: { en: "Request information", zh: "要求补充材料" },
  record_decision: { en: "Record decision", zh: "录入结论" },
  internal_note: { en: "Internal note", zh: "内部备注" },
  copilot: { en: "Claims copilot", zh: "理赔助手" },
  audit: { en: "Audit history", zh: "审计记录" },
};

export function tr(key: string, lang: Lang): string {
  const e = dict[key];
  if (!e) return key;
  return e[lang] || e.en;
}

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
  pick: (obj: unknown) => string;
}

const Ctx = createContext<LangCtx>({ lang: "en", setLang: () => {}, t: (k) => k, pick: () => "" });

export function LangProvider({ children, initial }: { children: ReactNode; initial?: Lang }) {
  const [lang, setLangState] = useState<Lang>(() => (initial || (localStorage.getItem("lantern.lang") as Lang) || "en"));
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    localStorage.setItem("lantern.lang", l);
    document.documentElement.lang = l === "zh" ? "zh-CN" : "en";
  }, []);
  const value = useMemo<LangCtx>(
    () => ({
      lang,
      setLang,
      t: (k: string) => tr(k, lang),
      pick: (obj: unknown) => {
        if (!obj) return "";
        if (typeof obj === "string") return obj;
        const o = obj as Record<string, string>;
        return o[lang] || o.en || Object.values(o)[0] || "";
      },
    }),
    [lang, setLang],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang() {
  return useContext(Ctx);
}
