import { useState } from 'react';

export function LegalNotices() {
  const [text, setText] = useState(''), [error, setError] = useState(false);
  const load = async () => {
    if (text) return;
    setError(false);
    try {
      const response = await fetch('./legal/THIRD_PARTY_NOTICES.txt');
      if (!response.ok) throw new Error('Missing license notices');
      setText(await response.text());
    } catch { setError(true); }
  };
  return <details onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>软件与组件许可证</summary>
    {error ? <p>许可证暂时无法读取。<button onClick={() => void load()}>重试</button></p> :
      text ? <pre style={{ maxHeight: '16rem', overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: '12px' }}>{text}</pre> : <p>正在读取许可证…</p>}
  </details>;
}
