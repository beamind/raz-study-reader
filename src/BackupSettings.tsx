import { useRef, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import type { Library, ReaderState } from './types';
import type { PracticeState } from './practice';
import { createBackup, MAX_BACKUP_BYTES, mergeBackup, openNativeBackup, parseBackup, saveBackup, type LearningBackup } from './backup';

export function BackupSettings({ reader, practice, library, onImport }: {
  reader: ReaderState; practice: PracticeState; library: Library;
  onImport: (records: { reader: ReaderState; practice: PracticeState }) => Promise<void>;
}) {
  const [backup, setBackup] = useState<LearningBackup | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const report = (error: unknown) => { setError(true); setMessage(error instanceof Error ? error.message : '操作未完成，请重试。'); };
  const preview = (text: string) => {
    const data = parseBackup(text);
    mergeBackup(data, reader, practice, library); // Check compatibility before presenting confirmation.
    setBackup(data); setMessage(''); setError(false);
  };
  const exportRecords = async () => {
    setBusy(true); setMessage(''); setError(false);
    try { if (await saveBackup(createBackup(reader, practice, library))) setMessage('备份文件已导出，请妥善保存。'); }
    catch (error) { report(error); }
    finally { setBusy(false); }
  };
  const choose = async () => {
    setBackup(null); setMessage(''); setError(false);
    if (Capacitor.getPlatform() !== 'android') { input.current?.click(); return; }
    setBusy(true);
    try { const text = await openNativeBackup(); if (text !== null) preview(text); }
    catch (error) { report(error); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!backup) return;
    setBusy(true); setMessage(''); setError(false);
    try {
      await onImport(mergeBackup(backup, reader, practice, library));
      setBackup(null); setMessage('记录已合并，同一轮练习不会重复计入。');
    } catch (error) { report(error); }
    finally { setBusy(false); }
  };
  const records = backup ? [...Object.values(backup.practice.books), backup.practice.review, backup.practice.mistakes] : [];
  const unknownBooks = backup ? Object.keys(backup.reader.progress).filter(id => !library.books.some(b => b.id === id)).length : 0;
  return <section className="backup-settings" aria-labelledby="backup-title">
    <h3 id="backup-title">学习记录备份</h3>
    <p>保存阅读位置、字号、练习、错题与复习记录，换设备后可以恢复。</p>
    <div className="backup-actions"><button disabled={busy} onClick={() => void exportRecords()}>导出学习记录</button>
      <button disabled={busy} onClick={() => void choose()}>导入学习记录</button></div>
    <input ref={input} hidden type="file" accept=".json,application/json" aria-label="选择学习记录备份" onChange={event => {
      const file = event.target.files?.[0]; event.target.value = '';
      if (!file) return;
      if (file.size > MAX_BACKUP_BYTES) { report(new Error('备份文件超过 20 MB。')); return; }
      setBusy(true); void file.text().then(preview).catch(report).finally(() => setBusy(false));
    }}/>
    {backup && <div className="backup-preview" aria-label="备份预览">
      <p>备份时间：{new Date(backup.exportedAt).toLocaleString('zh-CN')}</p>
      <p>{Object.keys(backup.reader.progress).length} 本阅读记录 · {records.reduce((sum, r) => sum + r.sessions.length, 0)} 轮练习</p>
      <p>合并后保留时间较新的阅读位置与已有练习，并恢复备份中的字号。</p>
      {unknownBooks > 0 && <p>其中 {unknownBooks} 本书当前未导入；阅读位置和练习历史仍会保留，导入对应资源包后可继续。</p>}
      <div className="backup-actions"><button disabled={busy} onClick={() => setBackup(null)}>取消导入</button>
        <button disabled={busy} onClick={() => void apply()}>确认合并记录</button></div>
    </div>}
    {busy && <p role="status">正在处理记录，请稍候…</p>}
    {message && <p role={error ? 'alert' : 'status'}>{message}</p>}
  </section>;
}
