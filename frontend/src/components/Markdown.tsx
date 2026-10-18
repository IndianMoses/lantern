import { createContext, useContext, useState, type ReactNode } from "react";
import { useLang } from "../lib/i18n";
import { Drawer, Badge } from "./ui";

export interface ClauseRef {
  clause_id?: string;
  citation: string;
  policy_number?: string;
  policy_name?: string;
  clause_ref?: string;
  title?: string;
  section?: string;
  kind?: string;
  text?: string;
  excerpt?: string;
  language?: string;
  tags?: string[];
  amount?: number | null;
  translations?: Record<string, string>;
}

const CiteCtx = createContext<{ lookup: (c: string) => ClauseRef | undefined; open: (c: ClauseRef) => void }>({ lookup: () => undefined, open: () => {} });

export function CitationProvider({ clauses, children }: { clauses: ClauseRef[]; children: ReactNode }) {
  const [current, setCurrent] = useState<ClauseRef | null>(null);
  const { lang, t } = useLang();
  const [showTranslation, setShow] = useState(false);
  const map = new Map<string, ClauseRef>();
  for (const c of clauses) map.set(c.citation.replace(/\s+/g, ""), c);
  const lookup = (c: string) => map.get(c.replace(/\s+/g, ""));
  const open = (c: ClauseRef) => {
    setShow(false);
    setCurrent(c);
  };
  const tr = current?.translations?.[lang === "zh" ? "zh" : "en"];
  return (
    <CiteCtx.Provider value={{ lookup, open }}>
      {children}
      {current && (
        <Drawer title={current.citation} onClose={() => setCurrent(null)}>
          <div className="stack">
            <div className="row">
              {current.kind && <Badge tone={current.kind === "exclusion" ? "bad" : current.kind === "coverage" ? "ok" : "info"}>{current.kind}</Badge>}
              {current.policy_name && <span className="small muted">{current.policy_name}</span>}
            </div>
            <h2 style={{ fontSize: 18 }}>{current.title}</h2>
            {current.section && <div className="small muted">{current.section}</div>}
            <p style={{ whiteSpace: "pre-wrap" }}>{showTranslation && tr ? tr : current.text || current.excerpt}</p>
            {tr && current.language !== lang && (
              <button className="btn sm" onClick={() => setShow((s) => !s)}>{showTranslation ? t("original") : t("translate")}</button>
            )}
            {current.amount != null && current.amount > 0 && <div className="small muted">Amount stated: {current.amount.toLocaleString()}</div>}
            {current.tags && current.tags.length > 0 && <div className="small muted">Tags: {current.tags.join(", ")}</div>}
          </div>
        </Drawer>
      )}
    </CiteCtx.Provider>
  );
}

export function CitationChip({ citation, unverified }: { citation: string; unverified?: boolean }) {
  const { lookup, open } = useContext(CiteCtx);
  const ref = lookup(citation);
  if (unverified || !ref) return <span className={`chip ${unverified ? "unverified" : ""}`} title={unverified ? "This citation could not be verified against the retrieved wording" : citation}>{citation}{unverified ? " ?" : ""}</span>;
  return (
    <button className="chip" onClick={() => open(ref)} title={ref.title}>
      {citation}
    </button>
  );
}

const CITE = /\[([A-Z]{2,6}(?:-[A-Z0-9]+){1,5}\s*§\s*[0-9]{1,3}(?:\.[0-9]{1,3}){0,2})(\s*—\s*(?:unverified|未核实))?\]/g;

function cites(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(CITE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(<span key={`${key}-t${i++}`}>{text.slice(last, idx)}</span>);
    out.push(<CitationChip key={`${key}-c${i++}`} citation={m[1].replace(/\s*§\s*/, " §")} unverified={!!m[2]} />);
    last = idx + m[0].length;
  }
  if (last < text.length) out.push(<span key={`${key}-t${i++}`}>{text.slice(last)}</span>);
  return out;
}

function inline(text: string, key: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g);
  const out: ReactNode[] = [];
  parts.filter(Boolean).forEach((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) out.push(<strong key={`${key}-b${i}`}>{cites(p.slice(2, -2), `${key}-b${i}`)}</strong>);
    else if (/^`[^`]+`$/.test(p)) out.push(<code key={`${key}-k${i}`}>{p.slice(1, -1)}</code>);
    else if (/^\*[^*\s][^*]*\*$/.test(p)) out.push(<em key={`${key}-i${i}`}>{cites(p.slice(1, -1), `${key}-i${i}`)}</em>);
    else out.push(...cites(p, `${key}-${i}`));
  });
  return out;
}

/** Inline text with citation chips and light emphasis, no block structure. */
export function RichText({ text }: { text: string }) {
  return <>{inline(text || "", "rt")}</>;
}

export function Markdown({ text }: { text: string }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const nodes: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (/^#{1,4}\s/.test(line)) {
      nodes.push(<h4 key={k++}>{inline(line.replace(/^#+\s/, ""), `h${k}`)}</h4>);
      i++;
      continue;
    }
    if (line.startsWith(">")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      nodes.push(<blockquote key={k++}>{buf.join("\n")}</blockquote>);
      continue;
    }
    if (/^\s*[-•]\s/.test(line)) {
      const items: ReactNode[] = [];
      while (i < lines.length && /^\s*[-•]\s/.test(lines[i])) {
        const depth = lines[i].match(/^\s*/)?.[0].length || 0;
        const content = lines[i].replace(/^\s*[-•]\s/, "");
        items.push(
          <li key={k++} style={{ marginLeft: depth > 0 ? 14 : 0 }}>
            {inline(content, `li${k}`)}
          </li>,
        );
        i++;
      }
      nodes.push(<ul key={k++}>{items}</ul>);
      continue;
    }
    if (/^\s*\d+[.)]\s/.test(line)) {
      const items: ReactNode[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s/.test(lines[i])) {
        items.push(<li key={k++}>{inline(lines[i].replace(/^\s*\d+[.)]\s/, ""), `ol${k}`)}</li>);
        i++;
      }
      nodes.push(<ol key={k++}>{items}</ol>);
      continue;
    }
    const buf: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|>|\s*[-•]\s|\s*\d+[.)]\s)/.test(lines[i])) {
      buf.push(lines[i]);
      i++;
    }
    nodes.push(<p key={k++}>{inline(buf.join(" "), `p${k}`)}</p>);
  }
  return <div className="md">{nodes}</div>;
}
