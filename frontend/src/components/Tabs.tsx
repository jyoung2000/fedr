import { useRef } from "react";

export interface TabItem {
  key: string;
  label: string;
}

/** Accessible tab list with arrow-key navigation. */
export function Tabs({ tabs, value, onChange, ariaLabel }: { tabs: TabItem[]; value: string; onChange: (key: string) => void; ariaLabel: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent, idx: number) => {
    let next = idx;
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    onChange(tabs[next].key);
    ref.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };
  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel} ref={ref}>
      {tabs.map((t, i) => (
        <button key={t.key} role="tab" type="button" aria-selected={t.key === value} tabIndex={t.key === value ? 0 : -1} onClick={() => onChange(t.key)} onKeyDown={(e) => onKey(e, i)} id={`tab-${t.key}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}
