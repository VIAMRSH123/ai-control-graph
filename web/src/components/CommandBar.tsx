import { useEffect, useRef, useState } from 'react';
import { api, FRAMEWORK_LABEL, type SearchHit } from '../api';

/* One input. It accepts a native reference ("A.6.2.8", "Art.14"), a concept
 * ("human oversight"), or any words from a control's text, because a person
 * asking about a control does not reliably know which of those they have.
 */

interface Props {
  onPick: (hit: SearchHit) => void;
}

export function CommandBar({ onPick }: Props) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);

  useEffect(() => {
    const onClickAway = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClickAway);
    return () => document.removeEventListener('mousedown', onClickAway);
  }, []);

  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setHits([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api
        .search(term)
        .then((results) => {
          if (cancelled) return;
          setHits(results);
          setActive(0);
          setOpen(true);
        })
        .catch(() => setHits([]));
    }, 140);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  const choose = (hit: SearchHit) => {
    onPick(hit);
    setQuery(`${hit.native_ref} — ${hit.title}`);
    setOpen(false);
  };

  return (
    <div className="commandbar" ref={boxRef}>
      <span className="commandbar-icon" aria-hidden="true">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" strokeLinecap="round" />
        </svg>
      </span>
      <input
        ref={inputRef}
        value={query}
        placeholder="Ask for a control — A.6.2.8, Art.14, or “human oversight”"
        aria-label="Search for a control"
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => hits.length && setOpen(true)}
        onKeyDown={(event) => {
          if (!open || !hits.length) return;
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActive((i) => (i + 1) % hits.length);
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive((i) => (i - 1 + hits.length) % hits.length);
          } else if (event.key === 'Enter') {
            event.preventDefault();
            choose(hits[active]);
          } else if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
      />
      {open && hits.length > 0 && (
        <div className="suggestions" role="listbox">
          {hits.map((hit, index) => (
            <button
              key={hit.id}
              className="suggestion"
              data-active={index === active}
              role="option"
              aria-selected={index === active}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(hit)}
            >
              <code>{hit.native_ref}</code>
              <span>
                <span className="s-title">{hit.title}</span>
                <span className="s-fw">{FRAMEWORK_LABEL[hit.framework_key]}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
