import { useState } from "react";
import { IconCheck, IconCopy } from "./Icons";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setDone(true);
      window.setTimeout(() => setDone(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button type="button" className="btn btn-sm" onClick={copy} aria-label={done ? "Copied" : label}>
      {done ? <IconCheck /> : <IconCopy />}
      {done ? "Copied" : label}
    </button>
  );
}
