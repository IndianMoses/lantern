import { useEffect, useState } from "react";
import { Icon } from "../components/Icons";
import { CustomerShell } from "../components/Layout";
import { Field, Spinner, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useLang } from "../lib/i18n";

interface Profile { country: string; city: string | null; currency: string; housing: string | null; household: Record<string, number>; assets: { type: string; label: string; value?: number | null; notes?: string; destinations?: string[] }[]; activities: { type: string; label: string; destinations?: string[]; trips_per_year?: number }[]; concerns: string[]; preferences: Record<string, unknown>; consents: Record<string, unknown> }

const ASSET_TYPES = ["home", "contents", "car", "bicycle", "ebike", "phone", "laptop", "jewellery", "pet", "other"];
const ACTIVITY_TYPES = ["travel", "work_from_home", "hybrid_work", "cycling_commute", "commuting_by_car", "delivery_rider", "charges_battery_at_home", "other"];

export default function SettingsPage() {
  const { t, lang } = useLang();
  const { user } = useAuth();
  const toast = useToast();
  const [p, setP] = useState<Profile | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<{ profile: Profile | null }>("/api/profile").then((r) => setP(r.profile || { country: "GB", city: "", currency: "GBP", housing: null, household: { adults: 1, children: 0 }, assets: [], activities: [], concerns: [], preferences: { language: lang }, consents: {} }));
  }, []);
  if (!p) return <CustomerShell title={t("settings")}><Spinner /></CustomerShell>;
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...p, assets: p.assets.map((a) => ({ ...a, value: a.value === null || a.value === undefined || (a.value as unknown) === "" ? null : Number(a.value) })), preferences: { ...p.preferences, language: lang } };
      await api.put("/api/profile", body);
      toast(lang === "zh" ? "已保存" : "Saved");
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const exportData = async () => {
    const data = await api.get<unknown>("/api/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "lantern-export.json";
    a.click();
  };
  return (
    <CustomerShell title={t("settings")} subtitle={lang === "zh" ? "只收集缺口分析和理赔需要的信息。" : "Only what the gap analysis and claims need. Nothing else is collected."} actions={<button className="btn primary" disabled={busy} onClick={save}>{t("save")}</button>}>
      <div className="grid cols-2">
        <div className="card">
          <h3>{t("profile")}</h3>
          <div className="grid cols-2">
            <Field label={lang === "zh" ? "国家/地区" : "Country"}><select className="select" value={p.country} onChange={(e) => setP({ ...p, country: e.target.value, currency: e.target.value === "CN" ? "CNY" : e.target.value === "HK" ? "HKD" : "GBP" })}><option value="GB">United Kingdom</option><option value="CN">中国大陆</option><option value="HK">Hong Kong</option><option value="SG">Singapore</option></select></Field>
            <Field label={lang === "zh" ? "城市" : "City"}><input className="input" value={p.city || ""} onChange={(e) => setP({ ...p, city: e.target.value })} /></Field>
            <Field label={lang === "zh" ? "居住情况" : "Housing"}><select className="select" value={p.housing || ""} onChange={(e) => setP({ ...p, housing: e.target.value || null })}><option value="">—</option><option value="owner">{lang === "zh" ? "自有住房" : "Owner"}</option><option value="renter">{lang === "zh" ? "租房" : "Renter"}</option><option value="living_with_family">{lang === "zh" ? "与家人同住" : "Living with family"}</option></select></Field>
            <Field label={lang === "zh" ? "家庭成员（成人/儿童）" : "Household (adults / children)"}><div className="row"><input className="input" type="number" min={1} value={p.household.adults ?? 1} onChange={(e) => setP({ ...p, household: { ...p.household, adults: Number(e.target.value) } })} /><input className="input" type="number" min={0} value={p.household.children ?? 0} onChange={(e) => setP({ ...p, household: { ...p.household, children: Number(e.target.value) } })} /></div></Field>
          </div>
          <div className="small muted" style={{ marginTop: 10 }}>{user?.email} · {t("language")}: {lang === "zh" ? "中文" : "English"}</div>
          <div className="row" style={{ marginTop: 14 }}><button className="btn sm" onClick={exportData}>{t("export_data")}</button></div>
        </div>
        <div className="card">
          <h3>{t("activities")}</h3>
          <div className="stack" style={{ gap: 8 }}>
            {p.activities.map((a, i) => (
              <div key={i} className="row">
                <select className="select" style={{ maxWidth: 200 }} value={a.type} onChange={(e) => setP({ ...p, activities: p.activities.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)) })}>{ACTIVITY_TYPES.map((x) => <option key={x} value={x}>{x.replace(/_/g, " ")}</option>)}</select>
                <input className="input" placeholder={lang === "zh" ? "说明（例如：每年去美国旅行一次）" : "Detail (e.g. one trip to the USA a year)"} value={a.label} onChange={(e) => setP({ ...p, activities: p.activities.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
                {a.type === "travel" && <input className="input" style={{ maxWidth: 160 }} placeholder="USA, Spain" value={(a.destinations || []).join(", ")} onChange={(e) => setP({ ...p, activities: p.activities.map((x, j) => (j === i ? { ...x, destinations: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) } : x)) })} />}
                <button className="btn sm icon" aria-label={t("delete")} onClick={() => setP({ ...p, activities: p.activities.filter((_, j) => j !== i) })}><Icon name="x" size={14} /></button>
              </div>
            ))}
            <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => setP({ ...p, activities: [...p.activities, { type: "travel", label: "" }] })}>+ {lang === "zh" ? "添加" : "Add"}</button>
          </div>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h3>{t("assets")}</h3>
        <table className="table">
          <thead><tr><th>{lang === "zh" ? "类型" : "Type"}</th><th>{lang === "zh" ? "说明" : "Label"}</th><th>{lang === "zh" ? "价值" : "Value"}</th><th>{lang === "zh" ? "备注" : "Notes"}</th><th></th></tr></thead>
          <tbody>
            {p.assets.map((a, i) => (
              <tr key={i}>
                <td><select className="select" value={a.type} onChange={(e) => setP({ ...p, assets: p.assets.map((x, j) => (j === i ? { ...x, type: e.target.value } : x)) })}>{ASSET_TYPES.map((x) => <option key={x} value={x}>{x}</option>)}</select></td>
                <td><input className="input" value={a.label} onChange={(e) => setP({ ...p, assets: p.assets.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} /></td>
                <td><input className="input" type="number" value={a.value ?? ""} onChange={(e) => setP({ ...p, assets: p.assets.map((x, j) => (j === i ? { ...x, value: e.target.value === "" ? null : Number(e.target.value) } : x)) })} /></td>
                <td><input className="input" value={a.notes || ""} onChange={(e) => setP({ ...p, assets: p.assets.map((x, j) => (j === i ? { ...x, notes: e.target.value } : x)) })} /></td>
                <td><button className="btn sm icon" aria-label={t("delete")} onClick={() => setP({ ...p, assets: p.assets.filter((_, j) => j !== i) })}><Icon name="x" size={14} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="btn sm" style={{ marginTop: 8 }} onClick={() => setP({ ...p, assets: [...p.assets, { type: "other", label: "", value: null }] })}>+ {lang === "zh" ? "添加资产" : "Add asset"}</button>
      </div>
    </CustomerShell>
  );
}
