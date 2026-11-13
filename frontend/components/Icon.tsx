import type { SVGProps } from "react";

type IconName =
  | "dashboard"
  | "layers"
  | "grid"
  | "cpu"
  | "plug"
  | "puzzle"
  | "pulse"
  | "plus"
  | "external"
  | "refresh"
  | "play"
  | "stop"
  | "check"
  | "close"
  | "chevron-right"
  | "chevron-down"
  | "search"
  | "settings"
  | "sun"
  | "moon"
  | "monitor"
  | "folder"
  | "folder-open"
  | "file"
  | "code"
  | "terminal"
  | "database"
  | "cloud"
  | "globe"
  | "trash"
  | "arrow-right"
  | "copy"
  | "save"
  | "rocket"
  | "shield"
  | "spark"
  | "route"
  | "clock"
  | "users"
  | "user"
  | "building"
  | "logout"
  | "key"
  | "lock"
  | "ticket"
  | "eye"
  | "pencil"
  | "download"
  | "upload";

const PATHS: Record<IconName, string> = {
  dashboard: "M3 3h7v7H3zM14 3h7v4h-7zM14 10h7v11h-7zM3 13h7v8H3z",
  layers: "M12 3 3 8l9 5 9-5-9-5ZM3 13l9 5 9-5M3 17.5l9 5 9-5",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  cpu: "M6 6h12v12H6zM9 9h6v6H9zM12 2v2M12 20v2M2 12h2M20 12h2M7 2v2M17 2v2M7 20v2M17 20v2M2 7h2M2 17h2M20 7h2M20 17h2",
  plug: "M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0V8ZM12 17v5",
  puzzle: "M10 3h4v3a2 2 0 1 0 4 0h3v4h-3a2 2 0 1 0 0 4h3v4h-4v-3a2 2 0 1 0-4 0v3H9v-4H6a2 2 0 1 1 0-4h3V6H6V3h4Z",
  pulse: "M2 12h4l3-8 4 16 3-8h6",
  plus: "M12 5v14M5 12h14",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  refresh: "M21 12a9 9 0 1 1-3-6.7M21 3v6h-6",
  play: "M7 4l13 8-13 8V4Z",
  stop: "M7 7h10v10H7z",
  check: "M4 12.5 9.5 18 20 6",
  close: "M6 6l12 12M18 6 6 18",
  "chevron-right": "M9 5l7 7-7 7",
  "chevron-down": "M5 9l7 7 7-7",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM20 20l-4.2-4.2",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11a2 2 0 1 1 0 4Z",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10Z",
  moon: "M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z",
  monitor: "M3 4h18v12H3zM8 20h8M12 16v4",
  folder: "M3 7a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z",
  "folder-open": "M3 8h6l2-2h8a1 1 0 0 1 1 1v2H3V8ZM3 9h18l-2 9H5L3 9Z",
  file: "M13 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8l-5-5ZM13 3v5h5",
  code: "M9 8l-4 4 4 4M15 8l4 4-4 4",
  terminal: "M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1ZM7 9l3 3-3 3M13 15h4",
  database: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3ZM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  cloud: "M7 18a4 4 0 0 1 .4-8A5.5 5.5 0 0 1 18 10.5 3.5 3.5 0 0 1 17.5 18H7Z",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18M12 3c2.5 2.4 3.8 5.5 3.8 9S14.5 18.6 12 21c-2.5-2.4-3.8-5.5-3.8-9S9.5 5.4 12 3Z",
  trash: "M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6",
  "arrow-right": "M5 12h14M13 6l6 6-6 6",
  copy: "M9 9h10v10H9zM5 15V5h10",
  save: "M5 3h11l3 3v15H5V3ZM8 3v6h7V3M8 15h8",
  rocket:
    "M12 3c3.5 2 5.5 5.5 5.5 9.5L12 18l-5.5-5.5C6.5 8.5 8.5 5 12 3ZM12 8.5v.01M6 16l-2 5 5-2",
  shield: "M12 3l8 3v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6l8-3Z",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 17h6a4 4 0 0 0 0-8h-4a4 4 0 0 1 0-8",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 8v5l3 2",
  users: "M16 19v-1.6A4.4 4.4 0 0 0 11.6 13H6.4A4.4 4.4 0 0 0 2 17.4V19M9 10.2a3.6 3.6 0 1 0 0-7.2 3.6 3.6 0 0 0 0 7.2ZM22 19v-1.6a4.4 4.4 0 0 0-3.3-4.2M16 3.2a3.6 3.6 0 0 1 0 6.9",
  user: "M20 20v-1.7A5.3 5.3 0 0 0 14.7 13H9.3A5.3 5.3 0 0 0 4 18.3V20M12 9.8a3.9 3.9 0 1 0 0-7.8 3.9 3.9 0 0 0 0 7.8Z",
  building: "M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 9h4a1 1 0 0 1 1 1v11M3 21h18M7.5 8h3M7.5 12h3M7.5 16h3",
  logout: "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h9",
  key: "M14.5 3a6.5 6.5 0 1 0-3.2 12.1L4 22.4 2.6 21l1.9-1.9L3 17.6l1.4-1.4 1.5 1.5 2.2-2.2A6.5 6.5 0 0 0 14.5 3ZM16.5 7.5h.01",
  lock: "M6 10h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1ZM8 10V7a4 4 0 0 1 8 0v3",
  ticket: "M3 9V7a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2a3 3 0 0 0 0 6v2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2a3 3 0 0 0 0-6ZM12 6v12",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7ZM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  pencil: "M4 20l4-1L20.5 6.5a2.1 2.1 0 0 0-3-3L5 16l-1 4ZM14 7l3 3",
  download: "M12 3v12M7 11l5 5 5-5M4 20h16",
  upload: "M12 17V5M7 9l5-5 5 5",
};

export function Icon({
  name,
  size = 16,
  ...rest
}: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

export type { IconName };
