/* Inline SVG icons (no icon packs). All are decorative unless given a title by the caller. */
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

export const IconDashboard = (p: P) => (
  <svg {...base} {...p}><rect x="3" y="3" width="8" height="8" rx="1.5" /><rect x="13" y="3" width="8" height="5" rx="1.5" /><rect x="13" y="12" width="8" height="9" rx="1.5" /><rect x="3" y="15" width="8" height="6" rx="1.5" /></svg>
);
export const IconOpportunities = (p: P) => (
  <svg {...base} {...p}><path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" /></svg>
);
export const IconTrading = (p: P) => (
  <svg {...base} {...p}><path d="M4 12h4l2-6 4 12 2-6h4" /></svg>
);
export const IconWallet = (p: P) => (
  <svg {...base} {...p}><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18" /><circle cx="16.5" cy="14.5" r="1.2" fill="currentColor" stroke="none" /></svg>
);
export const IconExchange = (p: P) => (
  <svg {...base} {...p}><path d="M4 7h13l-3-3" /><path d="M20 17H7l3 3" /></svg>
);
export const IconHistory = (p: P) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
);
export const IconSettings = (p: P) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
);
export const IconStop = (p: P) => (
  <svg {...base} {...p}><path d="M8 2h8l6 6v8l-6 6H8l-6-6V8z" /><path d="M9 9l6 6M15 9l-6 6" /></svg>
);
export const IconCopy = (p: P) => (
  <svg {...base} {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
);
export const IconCheck = (p: P) => (
  <svg {...base} {...p}><path d="M5 12l5 5L20 7" /></svg>
);
export const IconX = (p: P) => (
  <svg {...base} {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>
);
export const IconWarning = (p: P) => (
  <svg {...base} {...p}><path d="M12 3l10 18H2z" /><path d="M12 10v4M12 17.5v.5" /></svg>
);
export const IconChevron = (p: P) => (
  <svg {...base} {...p}><path d="M9 6l6 6-6 6" /></svg>
);
export const IconRefresh = (p: P) => (
  <svg {...base} {...p}><path d="M20 12a8 8 0 1 1-2.3-5.7" /><path d="M20 4v5h-5" /></svg>
);
export const IconDownload = (p: P) => (
  <svg {...base} {...p}><path d="M12 3v12" /><path d="M7 10l5 5 5-5" /><path d="M4 21h16" /></svg>
);
export const IconLock = (p: P) => (
  <svg {...base} {...p}><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>
);
