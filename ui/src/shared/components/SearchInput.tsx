import { useRef } from 'react';
import { Search, X } from 'lucide-react';

export function SearchInput({ value, onChange, placeholder, className = 'searchBox', iconSize = 16 }: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  className?: string;
  iconSize?: number;
}) {
  const input = useRef<HTMLInputElement>(null);

  return <div className={className} role="search">
    <Search size={iconSize} aria-hidden="true" />
    <input ref={input} aria-label="検索" value={value} placeholder={placeholder} onChange={event => onChange(event.target.value)} />
    {value && <button
      type="button"
      className="searchClearButton"
      aria-label="検索文字を消去"
      title="検索文字を消去"
      onClick={() => {
        onChange('');
        input.current?.focus();
      }}
    >
      <X className="searchClearIcon" aria-hidden="true" />
    </button>}
  </div>;
}
