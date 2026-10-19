import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setApiLang } from "./api";

export type Lang = "en" | "zh";

/** BCP 47 locale used for Intl formatting. */
export function localeOf(lang: Lang): string {
  return lang === "zh" ? "zh-CN" : "en-GB";
}

const dict: Record<string, { en: string; zh: string }> = {
  // navigation and shell
  home: { en: "Home", zh: "首页" },
  insurance: { en: "Insurance", zh: "保险" },
  more: { en: "More", zh: "更多" },
  menu: { en: "Menu", zh: "菜单" },
  notifications: { en: "Notifications", zh: "通知" },
  mark_all_read: { en: "Mark all read", zh: "全部标为已读" },
  nothing_yet: { en: "Nothing yet.", zh: "暂无内容。" },
  ref: { en: "Ref", zh: "编号" },
  updated: { en: "Updated", zh: "更新" },
  date: { en: "Date", zh: "日期" },
  progress: { en: "Progress", zh: "进度" },
  est_loss: { en: "Est. loss", zh: "预计损失" },
  customer_surface: { en: "Insurance assistant", zh: "保险助手" },
  insurer_surface: { en: "Insurer workspace", zh: "保险公司工作台" },
  bank_surface: { en: "Bank operations", zh: "银行运营" },
  language_label: { en: "Language", zh: "语言" },
  ask_assistant: { en: "Ask the assistant", zh: "问助手" },
  open_gap_analysis: { en: "Open gap analysis", zh: "打开缺口分析" },
  potential_not_determinations: { en: "Potential issues, not coverage determinations.", zh: "以上为潜在问题，不是保障结论。" },
  renewals_within_60: { en: "renewal(s) within 60 days", zh: "份保单60天内到期" },
  potential_gaps: { en: "potential gap(s)", zh: "个潜在缺口" },
  no_gaps_flagged: { en: "no gaps flagged", zh: "无缺口" },
  no_analysis_yet: { en: "no analysis yet", zh: "尚未分析" },
  active_label: { en: "Active", zh: "有效" },
  claim_word: { en: "claim", zh: "理赔" },
  actions_pending: { en: "action(s) pending", zh: "项待办" },
  priority_high: { en: "high", zh: "高" },
  priority_medium: { en: "medium", zh: "中" },
  priority_low: { en: "low", zh: "低" },
  integration: { en: "Integration", zh: "集成" },
  date_label: { en: "Date", zh: "日期" },
  location: { en: "Location", zh: "地点" },
  what_happened: { en: "What happened", zh: "经过" },
  items: { en: "Items", zh: "涉及物品" },
  no_claims_yet: { en: "No claims yet.", zh: "还没有理赔。" },
  file: { en: "File", zh: "文件" },
  type: { en: "Type", zh: "类型" },
  linked_to: { en: "Linked to", zh: "关联" },
  text_extraction: { en: "Text", zh: "文字提取" },
  uploaded: { en: "Uploaded", zh: "上传时间" },
  shared_with_insurer: { en: "shared with insurer", zh: "已发送给保险公司" },
  delete: { en: "Delete", zh: "删除" },
  claim: { en: "claim", zh: "理赔" },
  policy: { en: "policy", zh: "保单" },
  no_documents: { en: "No documents yet.", zh: "暂无文件" },
  instruction_text_ignored: { en: "instruction like text (ignored)", zh: "含类似指令的文字（已忽略）" },
  findings: { en: "Findings", zh: "发现" },
  show_covered: { en: "Show covered items", zh: "显示已保障项" },
  analysis_workspace_hint: { en: "Your declared assets and activities, checked clause by clause against every active policy.", zh: "把你申报的资产和活动与每份保单的条款逐一对照。" },
  risk_graph: { en: "Risk and coverage graph", zh: "风险与保障图谱" },
  analysis_progress: { en: "Analysis progress", zh: "分析进度" },
  risk_priority: { en: "Risk priority", zh: "风险优先级" },
  evidence_confidence: { en: "Evidence confidence", zh: "证据可信度" },
  verification: { en: "Verification", zh: "核验" },
  assumptions: { en: "Assumptions", zh: "假设" },
  missing_information: { en: "Missing information", zh: "缺少的信息" },
  next_step: { en: "Suggested next step", zh: "建议下一步" },
  request_review: { en: "Request human review", zh: "申请人工复核" },
  review_requested: { en: "Review requested", zh: "已申请复核" },
  illustrative: { en: "Illustrative only, not an approved payout", zh: "仅为示意，不是核准赔付" },
  filter: { en: "Filter", zh: "筛选" },
  all: { en: "All", zh: "全部" },
  list_view: { en: "List view", zh: "列表视图" },
  graph_view: { en: "Graph view", zh: "图谱视图" },
  zoom_in: { en: "Zoom in", zh: "放大" },
  zoom_out: { en: "Zoom out", zh: "缩小" },
  reset_view: { en: "Reset view", zh: "重置视图" },
  relationships: { en: "Relationships", zh: "关联关系" },
  stage_customer_context: { en: "Reading your profile and policies", zh: "读取你的资料和保单" },
  stage_extraction: { en: "Extracting coverage terms", zh: "提取保障条款" },
  stage_graph: { en: "Building the coverage graph", zh: "构建保障图谱" },
  stage_analysts: { en: "Generating independent scenarios", zh: "生成独立情景分析" },
  stage_challenge: { en: "Comparing model assessments", zh: "比较模型评估" },
  stage_judge: { en: "Adjudicating the evidence", zh: "裁定证据" },
  stage_verification: { en: "Verifying supporting evidence", zh: "核验支持证据" },
  stage_calculation: { en: "Calculating supported scenarios", zh: "计算可支持的情景" },
  stage_report: { en: "Preparing the final findings", zh: "整理最终发现" },
  models_used: { en: "Models used", zh: "使用的模型" },
  deterministic_mode: { en: "offline rules", zh: "离线规则" },
  integrity_checks: { en: "Integrity checks", zh: "完整性检查" },
  checks_note: { en: "Automated checks flag items for a person to look at. They never decide a claim or accuse anyone.", zh: "自动检查仅提示需要人工查看的事项，不会作出理赔结论或指控任何人。" },
  passed: { en: "Passed", zh: "通过" },
  warning: { en: "Warning", zh: "提示" },
  failed_validation: { en: "Failed validation", zh: "未通过校验" },
  inconclusive: { en: "Inconclusive", zh: "无法判断" },
  human_review_required: { en: "Human review required", zh: "需人工复核" },
  take_photo: { en: "Take a photo", zh: "拍照" },
  choose_file: { en: "Choose a file", zh: "选择文件" },
  install_app: { en: "Install the app", zh: "安装应用" },
  security: { en: "Security", zh: "安全" },
  sessions: { en: "Active sessions", zh: "活动会话" },
  revoke: { en: "Sign out", zh: "注销" },
  revoke_others: { en: "Sign out other devices", zh: "注销其他设备" },
  this_device: { en: "this device", zh: "本设备" },
  change_password: { en: "Change password", zh: "修改密码" },
  current_password: { en: "Current password", zh: "当前密码" },
  new_password: { en: "New password", zh: "新密码" },
  consents: { en: "Data sharing and consent", zh: "数据共享与授权" },
  account_security: { en: "Account security", zh: "账户安全" },
  mfa_code: { en: "Authenticator code", zh: "验证器验证码" },
  mfa_prompt: { en: "Enter the 6 digit code from your authenticator app.", zh: "请输入验证器应用中的 6 位验证码。" },
  verify: { en: "Verify", zh: "验证" },
  helpful: { en: "Helpful", zh: "有帮助" },
  not_helpful: { en: "Not helpful", zh: "没有帮助" },
  feedback_thanks: { en: "Thanks, recorded", zh: "已记录，谢谢" },
  consent_note: { en: "You decide what the platform may use and share. Each change is recorded.", zh: "由你决定平台可以使用和共享的内容，每次变更都会记录。" },
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
  /** BCP 47 locale for Intl formatting */
  locale: string;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
  pick: (obj: unknown) => string;
}

const Ctx = createContext<LangCtx>({ lang: "en", locale: "en-GB", setLang: () => {}, t: (k) => k, pick: () => "" });

function readStored(): Lang | null {
  try {
    const v = localStorage.getItem("lantern.lang");
    return v === "zh" || v === "en" ? v : null;
  } catch {
    return null;
  }
}

/**
 * One authoritative locale state for the whole application.
 * Every consumer subscribes through useLang(); the API client sends the
 * active language with each request so server rendered labels match, and
 * data hooks include `lang` in their dependencies so localised payloads are
 * fetched again when the language changes.
 */
export function LangProvider({ children, initial }: { children: ReactNode; initial?: Lang }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const l = initial || readStored() || "en";
    setApiLang(l);
    return l;
  });
  useEffect(() => {
    document.documentElement.lang = localeOf(lang);
  }, [lang]);
  const setLang = useCallback((l: Lang) => {
    // The API client must know the new language before any child effect
    // refetches (child effects run before this provider's own effects).
    setApiLang(l);
    setLangState(l);
    try {
      localStorage.setItem("lantern.lang", l);
    } catch {
      /* storage unavailable (private mode); state still updates */
    }
  }, []);
  const value = useMemo<LangCtx>(
    () => ({
      lang,
      locale: localeOf(lang),
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
