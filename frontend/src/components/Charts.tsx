// Small dependency free SVG charts for the operations dashboard.
const PALETTE = ["#1f5f5b", "#c8641a", "#2b5aa6", "#6b4fa0", "#b7791f", "#2f7d4f", "#b3372f", "#7a8089"];

export function BarChart({ data, height = 180, valueKey = "count", labelKey = "label", color, horizontal }: { data: Record<string, unknown>[]; height?: number; valueKey?: string; labelKey?: string; color?: string; horizontal?: boolean }) {
  const values = data.map((d) => Number(d[valueKey] || 0));
  const max = Math.max(1, ...values);
  if (horizontal) {
    const rowH = 26;
    const h = Math.max(height, data.length * rowH + 10);
    return (
      <svg className="chart" viewBox={`0 0 420 ${h}`} width="100%" height={h} role="img">
        {data.map((d, i) => {
          const w = (Number(d[valueKey] || 0) / max) * 230;
          return (
            <g key={i} transform={`translate(0, ${i * rowH + 6})`}>
              <text x="0" y="14" style={{ fontSize: 11 }}>{String(d[labelKey]).slice(0, 26)}</text>
              <rect x="150" y="3" width={w} height="16" rx="4" fill={color || PALETTE[i % PALETTE.length]} />
              <text x={150 + w + 6} y="15" style={{ fontSize: 11, fill: "var(--ink-2)" }}>{String(d[valueKey])}</text>
            </g>
          );
        })}
      </svg>
    );
  }
  const w = 480;
  const pad = 24;
  const bw = Math.min(46, (w - pad * 2) / Math.max(1, data.length) - 8);
  return (
    <svg className="chart" viewBox={`0 0 ${w} ${height}`} width="100%" height={height} role="img">
      {data.map((d, i) => {
        const v = Number(d[valueKey] || 0);
        const bh = (v / max) * (height - 50);
        const x = pad + i * ((w - pad * 2) / Math.max(1, data.length)) + 4;
        return (
          <g key={i}>
            <rect x={x} y={height - 30 - bh} width={bw} height={bh} rx="4" fill={color || PALETTE[i % PALETTE.length]} />
            <text x={x + bw / 2} y={height - 36 - bh} textAnchor="middle" style={{ fontSize: 11, fill: "var(--ink-2)" }}>{v}</text>
            <text x={x + bw / 2} y={height - 14} textAnchor="middle" style={{ fontSize: 10 }}>{String(d[labelKey]).slice(0, 14)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function LineChart({ data, height = 160, valueKey = "count", labelKey = "week", color = "#1f5f5b" }: { data: Record<string, unknown>[]; height?: number; valueKey?: string; labelKey?: string; color?: string }) {
  const w = 480;
  const pad = 28;
  const values = data.map((d) => Number(d[valueKey] || 0));
  const max = Math.max(1, ...values);
  const step = (w - pad * 2) / Math.max(1, data.length - 1);
  const pts = values.map((v, i) => [pad + i * step, height - 30 - (v / max) * (height - 50)] as const);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ");
  const area = `${path} L${pts[pts.length - 1]?.[0] ?? pad},${height - 30} L${pad},${height - 30} Z`;
  return (
    <svg className="chart" viewBox={`0 0 ${w} ${height}`} width="100%" height={height} role="img">
      <path d={area} fill={color} opacity="0.12" />
      <path d={path} fill="none" stroke={color} strokeWidth="2" />
      {pts.map((p, i) => (
        <g key={i}>
          <circle cx={p[0]} cy={p[1]} r="3" fill={color} />
          {values[i] > 0 && <text x={p[0]} y={p[1] - 8} textAnchor="middle" style={{ fontSize: 10, fill: "var(--ink-2)" }}>{values[i]}</text>}
          {i % 2 === 0 && <text x={p[0]} y={height - 12} textAnchor="middle" style={{ fontSize: 9 }}>{String(data[i][labelKey]).slice(5)}</text>}
        </g>
      ))}
    </svg>
  );
}

export function StackedBars({ data, keys, labelKey = "month", height = 170 }: { data: Record<string, unknown>[]; keys: string[]; labelKey?: string; height?: number }) {
  const w = 480;
  const pad = 24;
  const totals = data.map((d) => keys.reduce((s, k) => s + Number(d[k] || 0), 0));
  const max = Math.max(1, ...totals);
  const slot = (w - pad * 2) / Math.max(1, data.length);
  const bw = Math.min(50, slot - 10);
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${w} ${height}`} width="100%" height={height} role="img">
        {data.map((d, i) => {
          let y = height - 30;
          return (
            <g key={i}>
              {keys.map((k, ki) => {
                const v = Number(d[k] || 0);
                const bh = (v / max) * (height - 50);
                y -= bh;
                return <rect key={k} x={pad + i * slot + 5} y={y} width={bw} height={bh} fill={PALETTE[ki % PALETTE.length]} rx="2" />;
              })}
              <text x={pad + i * slot + 5 + bw / 2} y={height - 12} textAnchor="middle" style={{ fontSize: 10 }}>{String(d[labelKey])}</text>
            </g>
          );
        })}
      </svg>
      <div className="legend">
        {keys.map((k, i) => (
          <span key={k} style={{ ["--c" as string]: PALETTE[i % PALETTE.length] }}>{k.replace(/_/g, " ")}</span>
        ))}
      </div>
    </div>
  );
}
