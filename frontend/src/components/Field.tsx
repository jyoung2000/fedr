import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

interface FieldProps {
  label: ReactNode;
  help?: ReactNode;
  error?: string | null;
  unit?: string;
  children: (id: string) => ReactNode;
  className?: string;
}

/** Labelled form field with help text, unit suffix and error line. */
export function Field({ label, help, error, unit, children, className = "" }: FieldProps) {
  const id = useId();
  return (
    <div className={`field ${className}`}>
      <label htmlFor={id}>{label}</label>
      {unit ? (
        <div className="input-unit">
          {children(id)}
          <span className="unit">{unit}</span>
        </div>
      ) : (
        children(id)
      )}
      {help ? <span className="help">{help}</span> : null}
      {error ? (
        <span className="error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`input ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`input ${props.className ?? ""}`} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`input ${props.className ?? ""}`} />;
}

export function Checkbox({ label, checked, onChange, disabled, help }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; help?: ReactNode }) {
  return (
    <label className={`checkbox ${disabled ? "disabled" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {help ? <span className="help" style={{ display: "block" }}>{help}</span> : null}
      </span>
    </label>
  );
}
