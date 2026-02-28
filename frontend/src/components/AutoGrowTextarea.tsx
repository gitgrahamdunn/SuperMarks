import { ChangeEventHandler, useEffect, useRef } from 'react';

type AutoGrowTextareaProps = {
  id: string;
  label?: string;
  value: string;
  onChange: ChangeEventHandler<HTMLTextAreaElement>;
  placeholder?: string;
  minRows?: number;
  maxHeightPx?: number;
  className?: string;
  disabled?: boolean;
};

export function AutoGrowTextarea({
  id,
  label,
  value,
  onChange,
  placeholder,
  minRows = 6,
  maxHeightPx = 320,
  className,
  disabled,
}: AutoGrowTextareaProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!textareaRef.current) return;

    textareaRef.current.style.height = 'auto';
    textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, maxHeightPx)}px`;
    textareaRef.current.style.overflowY = textareaRef.current.scrollHeight > maxHeightPx ? 'auto' : 'hidden';
  }, [maxHeightPx, value]);

  const textarea = (
    <textarea
      ref={textareaRef}
      id={id}
      className={className}
      rows={minRows}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      disabled={disabled}
    />
  );

  if (label) {
    return (
      <label className="stack" htmlFor={id}>
        {label}
        {textarea}
      </label>
    );
  }

  return textarea;
}
