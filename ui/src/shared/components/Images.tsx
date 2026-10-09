import { useEffect, useRef, useState } from 'react';
import { makeApi } from '../api';

export function AuthedImage({
  api,
  path,
  onClick,
}: {
  api: ReturnType<typeof makeApi>;
  path: string;
  onClick?: () => void;
}) {
  const [src, setSrc] = useState('');

  useEffect(() => {
    let active = true;
    let url = '';
    setSrc('');
    api.blob(path)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => { if (active) setSrc(''); });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, path]);

  if (!src) return <div className="imageEmpty">画像待ち</div>;
  return <img className={onClick ? 'clickableImage' : undefined} src={src} alt="business card" onClick={onClick} />;
}

export function ThumbImage({
  api,
  cardId,
  version,
}: {
  api: ReturnType<typeof makeApi>;
  cardId: string;
  version?: string | number;
}) {
  const [src, setSrc] = useState('');
  const [visible, setVisible] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const element = rootRef.current;
    if (!element) return;
    if (!('IntersectionObserver' in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        setVisible(true);
        observer.disconnect();
      },
      { rootMargin: '240px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    let url = '';
    setSrc('');
    api.blob(`/api/cards/${cardId}/thumbnail${version ? `?v=${encodeURIComponent(String(version))}` : ''}`)
      .then((blob) => {
        if (!active) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch(() => { if (active) setSrc(''); });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, cardId, version, visible]);

  return (
    <span ref={rootRef} className="thumbSlot">
      {src ? <img className="thumb" src={src} alt="" /> : <span className="thumbPlaceholder" />}
    </span>
  );
}
