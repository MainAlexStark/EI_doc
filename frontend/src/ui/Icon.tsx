/** Единый набор иконок (контур 1.7, сетка 24) — вместо эмодзи и символов «☰ ✎ × ‹ ›». */

import type { CSSProperties } from "react";

const PATHS = {
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  checkCircle: <><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.7 2.7L16 9.8" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  chevronLeft: <path d="M15 6l-6 6 6 6" />,
  chevronRight: <path d="M9 6l6 6-6 6" />,
  chevronDown: <path d="M6 9l6 6 6-6" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  sidebar: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9.5 4.5v15" /></>,
  more: <><circle cx="5.5" cy="12" r="1.3" fill="currentColor" /><circle cx="12" cy="12" r="1.3" fill="currentColor" /><circle cx="18.5" cy="12" r="1.3" fill="currentColor" /></>,
  pen: <path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3zM13.5 7.5l3 3" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  star: <path d="M12 3.8l2.5 5.1 5.6.8-4.1 4 1 5.6-5-2.7-5 2.7 1-5.6-4.1-4 5.6-.8z" />,
  pin: <path d="M9 3.5h6M10 3.5v5.2L6.5 13h11L14 8.7V3.5M12 13v7.5" />,
  refresh: <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4" />,
  download: <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M4.5 15v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />,
  upload: <path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M4.5 15v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />,
  file: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>,
  doc: <><rect x="5" y="3" width="14" height="18" rx="2.5" /><path d="M9 8h6M9 12h6M9 16h3" /></>,
  folder: <path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  inbox: <><path d="M3 13h5l1.5 2.5h5L16 13h5" /><path d="M5 5h14l2 8v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z" /></>,
  calendar: <><rect x="3.5" y="5" width="17" height="15" rx="2.5" /><path d="M3.5 10h17M8 3v4M16 3v4" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  tasks: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" /></>,
  sparkle: <path d="M12 4l1.6 4.4L18 10l-4.4 1.6L12 16l-1.6-4.4L6 10l4.4-1.6z" />,
  gauge: <><circle cx="12" cy="12" r="8" /><path d="M12 12l4-3" /></>,
  shield: <path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z" />,
  book: <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5zM5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3" />,
  memory: <><path d="M12 3a6 6 0 0 0-6 6c0 2.2 1.2 3.6 2.2 4.6.6.6.8 1.2.8 1.9V17h6v-1.5c0-.7.2-1.3.8-1.9 1-1 2.2-2.4 2.2-4.6a6 6 0 0 0-6-6z" /><path d="M9.5 20.5h5" /></>,
  plug: <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4" />,
  tools: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L4 16.8V20h3.2l5.3-5.3a4 4 0 0 0 5.2-5.4l-2.4 2.4-2.3-.6-.6-2.3z" />,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>,
  chart: <path d="M4 20h16M7 16v-4M12 16V8M17 16v-7" />,
  users: <><circle cx="9" cy="8.5" r="3.5" /><path d="M2.5 19.5c1-3.3 3.6-5 6.5-5s5.5 1.7 6.5 5M16 5.2a3.5 3.5 0 0 1 0 6.6M18 14.8c1.8.7 3 2.3 3.5 4.7" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 20c1.5-3.5 4.5-5 8-5s6.5 1.5 8 5" /></>,
  logout: <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H4" />,
  phone: <path d="M5 4.5h3l1.5 4-2 1.3a11 11 0 0 0 6.7 6.7l1.3-2 4 1.5v3a1.5 1.5 0 0 1-1.6 1.5A16.5 16.5 0 0 1 3.5 6.1 1.5 1.5 0 0 1 5 4.5z" />,
  mail: <><rect x="3.5" y="5.5" width="17" height="13" rx="2" /><path d="M4 7l8 6 8-6" /></>,
  mapPin: <><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>,
  globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.5 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.5-3.5-8.5s1-5.9 3.5-8.5z" /></>,
  car: <><path d="M5 16.5V12l1.8-4.6A2 2 0 0 1 8.7 6h6.6a2 2 0 0 1 1.9 1.4L19 12v4.5" /><path d="M3.5 16.5h17v2a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1v-1h-9v1a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1zM5 12h14" /><circle cx="8" cy="14.5" r=".6" fill="currentColor" /><circle cx="16" cy="14.5" r=".6" fill="currentColor" /></>,
  briefcase: <><rect x="3.5" y="7" width="17" height="12.5" rx="2" /><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3.5 12.5h17" /></>,
  camera: <><path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.2l1.5-2.2h5.6L16.3 7h2.2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" /><circle cx="12" cy="12.8" r="3.3" /></>,
  printer: <><path d="M7 9V4h10v5M7 17H5a1.5 1.5 0 0 1-1.5-1.5v-5A1.5 1.5 0 0 1 5 9h14a1.5 1.5 0 0 1 1.5 1.5v5A1.5 1.5 0 0 1 19 17h-2" /><rect x="7" y="14" width="10" height="6" rx="1" /></>,
  wifi: <><path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.6 15.8a5 5 0 0 1 6.8 0" /><circle cx="12" cy="19" r="1" fill="currentColor" /></>,
  wifiOff: <><path d="M3 3l18 18M8.6 15.8a5 5 0 0 1 6.8 0M5.5 12.5a9.5 9.5 0 0 1 4.2-2.3M2.5 9a14 14 0 0 1 4.2-2.7M14 10.2a9.5 9.5 0 0 1 4.5 2.3M11 5.1A14 14 0 0 1 21.5 9" /><circle cx="12" cy="19" r="1" fill="currentColor" /></>,
  alert: <><path d="M12 4 2.8 19.5h18.4z" /><path d="M12 10v4.5M12 17.2h.01" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.5M12 7.8h.01" /></>,
  ban: <><circle cx="12" cy="12" r="8.5" /><path d="M6 6l12 12" /></>,
  send: <path d="M12 19V5M6 11l6-6 6 6" />,
  telegram: <path d="M20.5 4.5 3.5 11l5.5 2 2 6 3-3.8 4.5 3.3zM9 13l8.5-6" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></>,
  flag: <path d="M5 21V4M5 4h11l-2 4 2 4H5" />,
  layers: <path d="M12 3 3 8l9 5 9-5zM3 12.5l9 5 9-5M3 17l9 5 9-5" />,
  board: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9.2 4.5v15M14.8 4.5v15" /></>,
  list: <path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />,
  bolt: <path d="M13 3 5 13.5h6L10 21l8-10.5h-6z" />,
  lock: <><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" /></>,
  server: <><rect x="4" y="4.5" width="16" height="6" rx="1.5" /><rect x="4" y="13.5" width="16" height="6" rx="1.5" /><path d="M7.5 7.5h.01M7.5 16.5h.01" /></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6" />,
  undo: <path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" />,
  stop: <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />,
  paperclip: <path d="M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.2-8.2a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.6-7.6" />,
  rub: <path d="M8 20V4h5.5a4 4 0 0 1 0 8H6M6 16h8" />,
  // --- v2 (пересборка 21.11.2026) ---
  box: <><path d="M3.8 7.6 12 3.5l8.2 4.1v8.8L12 20.5l-8.2-4.1z" /><path d="M3.8 7.6 12 11.7l8.2-4.1M12 11.7v8.8M7.9 5.5l8.2 4.1" /></>,
  truck: <><path d="M3 6.5h10.5v9.5H3zM13.5 10h4l3 3.2V16h-7" /><circle cx="7" cy="17.5" r="1.8" /><circle cx="17" cy="17.5" r="1.8" /></>,
  map: <path d="M9 4.5 3.5 6.8v12.7L9 17.2l6 2.3 5.5-2.3V4.5L15 6.8zM9 4.5v12.7M15 6.8v12.7" />,
  route: <><circle cx="6" cy="18" r="2.2" /><circle cx="18" cy="6" r="2.2" /><path d="M8.2 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.8" /></>,
  building: <><path d="M4.5 20.5V5.5a1.5 1.5 0 0 1 1.5-1.5h7.5a1.5 1.5 0 0 1 1.5 1.5v15M15 9.5h3a1.5 1.5 0 0 1 1.5 1.5v9.5M3 20.5h18" /><path d="M8 8h2.5M8 11.5h2.5M8 15h2.5" /></>,
  handshake: <path d="M2.5 11.5 6 8l3 1 3-2.5 3.2 1.1L18 8l3.5 3.5M6 8v6l4.5 4.2a1.3 1.3 0 0 0 1.9-.1l.4-.5M18 8v6l-3.4 3.4M9.5 12.5l2.8 2.6M12 10.8l3.4 3.1" />,
  contract: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M8.5 12.5h7M8.5 9h3M8.5 17c1-1.3 1.8-1.3 2.4 0s1.3 1.2 2.1-.2c.4-.7.9-.8 1.5-.3l1 .5" /></>,
  scan: <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M4 12h16" />,
  barcode: <path d="M4 5.5v13M7 5.5v13M10.5 5.5v13M13 5.5v13M16.5 5.5v13M20 5.5v13M8.7 5.5v13M18.3 5.5v13" strokeWidth={1.2} />,
  keyboard: <><rect x="2.5" y="6" width="19" height="12" rx="2" /><path d="M6 9.5h.01M9 9.5h.01M12 9.5h.01M15 9.5h.01M18 9.5h.01M6.5 12.5h.01M9.5 12.5h.01M12.5 12.5h.01M15.5 12.5h.01M8 15.2h8" /></>,
  command: <path d="M9 6.5V17.5M15 6.5v11M6.5 9h11M6.5 15h11M9 9H7.2A2.2 2.2 0 1 1 9 6.8zM15 9h1.8A2.2 2.2 0 1 0 15 6.8zM9 15H7.2A2.2 2.2 0 1 0 9 17.2zM15 15h1.8a2.2 2.2 0 1 1-1.8 2.2z" />,
  flask: <path d="M9.5 3.5h5M10.5 3.5v5.3L5 18.2a1.5 1.5 0 0 0 1.3 2.3h11.4a1.5 1.5 0 0 0 1.3-2.3l-5.5-9.4V3.5M7.6 14h8.8" />,
  thermometer: <><path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z" /><path d="M12 9v7" /></>,
  stamp: <path d="M9.5 13.5h5l-.8-3.2a3.5 3.5 0 1 0-3.4 0zM4.5 17a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v1.5h-15zM6 21h12" />,
  receipt: <path d="M6 3.5h12v17l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4zM9 8h6M9 11.5h6M9 15h3.5" />,
  filter: <path d="M4 5.5h16l-6.2 7.3v5.4l-3.6 1.8v-7.2z" />,
  home: <path d="M4 10.5 12 4l8 6.5V19a1.5 1.5 0 0 1-1.5 1.5H15v-6H9v6H5.5A1.5 1.5 0 0 1 4 19z" />,
  sun: <><circle cx="12" cy="12" r="3.8" /><path d="M12 2.8v2.1M12 19.1v2.1M2.8 12h2.1M19.1 12h2.1M5.5 5.5 7 7M17 17l1.5 1.5M5.5 18.5 7 17M17 7l1.5-1.5" /></>,
  moon: <path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" />,
  history: <><path d="M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5M3.5 4v4.5H8" /><path d="M12 8v4.3l3 1.7" /></>,
  arrowLeft: <path d="M19 12H5M11 6l-6 6 6 6" />,
  arrowUpRight: <path d="M7 17 17 7M8.5 7H17v8.5" />,
  clipboard: <><rect x="5" y="4.5" width="14" height="16.5" rx="2" /><path d="M9 4.5V3.5h6v1M9 4.5a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1M8.5 11h7M8.5 14.5h7M8.5 18h4" /></>,
  collapse: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9.5 4.5v15M15.5 10l-2 2 2 2" /></>,
  expand: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9.5 4.5v15M13.5 10l2 2-2 2" /></>,
  hash: <path d="M9.5 4 8 20M16 4l-1.5 16M4.5 9h15M4 15h15" />,
  target: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r=".9" fill="currentColor" /></>,
} as const;

export type IconName = keyof typeof PATHS;

export default function Icon({
  name, size = 18, className, style, title, strokeWidth = 1.7,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
  title?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      className={`icon ${className ?? ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      style={style}
    >
      {title && <title>{title}</title>}
      {PATHS[name]}
    </svg>
  );
}
