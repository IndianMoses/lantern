/** Consistent 24px outline icons (no emoji in the interface). */
import type { SVGProps } from "react";

const paths: Record<string, string> = {
  home: "M3 11.5 12 4l9 7.5M5 10v10h14V10",
  chat: "M4 5h16v11H9l-5 4z",
  shield: "M12 3 4 6v6c0 4.5 3.4 7.7 8 9 4.6-1.3 8-4.5 8-9V6z",
  claims: "M7 3h7l5 5v13H7zM14 3v5h5M9 13h6M9 17h6",
  folder: "M3 7h6l2 2h10v10H3z",
  inbox: "M4 5h16v14H4zM4 13h5l1.5 2h3L15 13h5",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4.5 12l-1.5-1 1-2.5 1.8.2a7 7 0 0 1 1.4-1.4l-.2-1.8L9.5 4l1 1.5a7 7 0 0 1 2 0l1-1.5 2.5 1-.2 1.8a7 7 0 0 1 1.4 1.4l1.8-.2 1 2.5-1.5 1a7 7 0 0 1 0 2l1.5 1-1 2.5-1.8-.2a7 7 0 0 1-1.4 1.4l.2 1.8-2.5 1-1-1.5a7 7 0 0 1-2 0l-1 1.5-2.5-1 .2-1.8a7 7 0 0 1-1.4-1.4l-1.8.2-1-2.5 1.5-1a7 7 0 0 1 0-2z",
  bell: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  paperclip: "m16 7-7.5 7.5a2.1 2.1 0 0 0 3 3L19 10a4.2 4.2 0 0 0-6-6l-8 8a6.4 6.4 0 0 0 9 9l5-5",
  x: "M6 6l12 12M18 6 6 18",
  menu: "M4 7h16M4 12h16M4 17h16",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  chevronDown: "m6 9 6 6 6-6",
  chevronUp: "m6 15 6-6 6 6",
  chevronRight: "m9 6 6 6-6 6",
  arrowLeft: "M19 12H5m6-6-6 6 6 6",
  arrowRight: "M5 12h14m-6-6 6 6-6 6",
  check: "m5 12 5 5L20 7",
  warning: "M12 3 2 20h20zM12 10v4m0 3h.01",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5m0-8h.01",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 17a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 2-4-4",
  logout: "M10 17l5-5-5-5M15 12H3M11 3h8v18h-8",
  external: "M14 4h6v6M20 4l-9 9M19 14v6H4V5h6",
  layers: "m12 3 9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  graph: "M6 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 21a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM8 14.5l8-6M8 16.5l8 2",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  users: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2 20a7 7 0 0 1 14 0M16 4a3.5 3.5 0 0 1 0 7M18 13a6 6 0 0 1 4 6",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  download: "M12 3v12m-5-5 5 5 5-5M4 21h16",
  upload: "M12 21V9m-5 5 5-5 5 5M4 3h16",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  mail: "M3 6h18v12H3zM3 7l9 6 9-6",
  file: "M7 3h7l5 5v13H7zM14 3v5h5",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5",
  filter: "M4 5h16l-6 8v6l-4-2v-4z",
  zoomIn: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 2-4-4M11 8v6M8 11h6",
  zoomOut: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 2-4-4M8 11h6",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17h.01",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
  bank: "M3 10h18L12 4zM5 10v8M9 10v8M15 10v8M19 10v8M3 20h18",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  key: "M15 13a5 5 0 1 0-4.6-3L3 17.4V21h3.6l1.4-1.4V17h2.6l1.4-1.4V13z",
  send: "M22 2 11 13M22 2 15 22l-4-9-9-4z",
  edit: "M4 20h4l10-10-4-4L4 16zM13 7l4 4",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
  sparkle: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6",
  thumbUp: "M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3zm0 0 4-8a2 2 0 0 1 2 2v4h5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 16.8 19H7",
  thumbDown: "M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-3zm0 0-4 8a2 2 0 0 1-2-2v-4H6a2 2 0 0 1-2-2.3l1.2-6A2 2 0 0 1 7.2 5H17",
};

export type IconName = keyof typeof paths;

export function Icon({ name, size = 18, className, ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg className={`ico ${className || ""}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" {...rest}>
      <path d={paths[name] || ""} />
    </svg>
  );
}
