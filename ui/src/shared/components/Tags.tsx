import React, { useState } from 'react';
import { parseTags } from '../format';

export function TagsInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [input, setInput] = useState('');
  const tags = parseTags(value);

  function commit(rawValue: string) {
    const nextTags = [...tags];
    for (const tag of parseTags(rawValue)) {
      if (!nextTags.includes(tag)) {
        nextTags.push(tag);
      }
    }
    onChange(nextTags.join(', '));
    setInput('');
  }

  function remove(tag: string) {
    onChange(tags.filter((current) => current !== tag).join(', '));
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Enter' || event.key === ',' || event.key === '、') {
      event.preventDefault();
      commit(input);
      return;
    }
    if (event.key === 'Backspace' && !input && tags.length) {
      event.preventDefault();
      remove(tags[tags.length - 1]);
    }
  }

  function onInputChange(nextValue: string) {
    if (/[,、\n\r]/.test(nextValue)) {
      commit(nextValue);
      return;
    }
    setInput(nextValue);
  }

  return (
    <div className="tagsInput">
      {tags.map((tag) => (
        <span className="tagPill tagPillEditable" key={tag}>
          {tag}
          <button type="button" onClick={() => remove(tag)} title={`${tag}を削除`}>
            ×
          </button>
        </span>
      ))}
      <input
        value={input}
        onChange={(event) => onInputChange(event.target.value)}
        onBlur={() => commit(input)}
        onKeyDown={onKeyDown}
        placeholder={tags.length ? '' : 'タグを入力'}
      />
    </div>
  );
}

export function TagList({ tags, showEmpty = true }: { tags?: string; showEmpty?: boolean }) {
  const items = parseTags(tags || '');
  if (!items.length) {
    return showEmpty ? <span className="emptyTags">-</span> : null;
  }
  return (
    <div className="tagList">
      {items.map((tag) => (
        <span className="tagPill" key={tag} title={tag}>{tag}</span>
      ))}
    </div>
  );
}
