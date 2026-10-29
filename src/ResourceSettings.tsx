import { useEffect, useRef, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { importNativeResourcePack, importWebResourcePack, listResourcePacks, removeResourcePack } from './resource-packs';
import type { InstalledPack } from './pack-format';

const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
export function ResourceSettings({ onChanged, onBusy }: { onChanged(): Promise<void>; onBusy(busy: boolean): void }) {
  const [packs, setPacks] = useState<InstalledPack[]>([]);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState(false);
  const [remove, setRemove] = useState<string | null>(null);
  const [usage, setUsage] = useState<StorageEstimate | null>(null);
  const [offlineReady, setOfflineReady] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const android = Capacitor.getPlatform() === 'android';
  useEffect(() => {
    let active = true;
    void listResourcePacks().then(packs => { if (active) setPacks(packs); })
      .catch(error => { if (active) { setError(true); setMessage(String(error.message)); } });
    if (!android) void navigator.storage?.estimate().then(usage => { if (active) setUsage(usage); }).catch(() => undefined);
    if (!android && import.meta.env.PROD && import.meta.env.MODE !== 'bundled' && 'serviceWorker' in navigator) {
      void navigator.serviceWorker.ready.then(() => { if (active) setOfflineReady(true); });
    }
    return () => { active = false; };
  }, [android]);
  const run = async (operation: () => Promise<boolean | void>) => {
    setBusy(true); onBusy(true); setError(false); setMessage('正在处理资源包…');
    try {
      if (await operation() === false) { setMessage(''); return; }
      setMessage('资源包已就绪，正在更新书架…');
      await onChanged();
    } catch (error) {
      setError(true); setMessage(error instanceof Error ? error.message : '资源包处理失败，请重试。');
    } finally { setBusy(false); onBusy(false); }
  };
  const choose = () => {
    if (android) void run(() => importNativeResourcePack(setMessage));
    else input.current?.click();
  };
  return <section className="resource-settings" aria-labelledby="resource-title">
    <h3 id="resource-title">按级别导入，离线阅读</h3>
    <p>选择电脑或 Pad 上的 .razpack 文件。封面、书页、讲义、音频和习题会一起导入；再次导入同一级别即可更新。</p>
    <button className="primary" disabled={busy} onClick={choose}>导入资源包</button>
    <input hidden ref={input} type="file" accept=".razpack,.zip,application/zip" aria-label="选择资源包" onChange={event => {
      const file = event.target.files?.[0]; event.target.value = '';
      if (file) void run(async () => {
        await navigator.storage?.persist?.();
        await importWebResourcePack(file, setMessage);
      });
    }}/>
    <div className="resource-pack-list">{packs.map(pack => <article className="resource-pack" key={pack.id}>
      <div><b>{pack.manifest.level.id} 级</b><span>{pack.manifest.library.books.length} 本 · {size(pack.manifest.totalBytes)}</span>
        <small>导入于 {new Date(pack.installedAt).toLocaleDateString('zh-CN')}</small></div>
      {remove === pack.manifest.level.id ? <div><p>移除本级资源，保留阅读和练习记录。</p>
        <button disabled={busy} onClick={() => void run(() => removeResourcePack(remove))}>确认移除 {remove} 级资源</button>
        <button disabled={busy} onClick={() => setRemove(null)}>取消移除</button></div> :
        <button disabled={busy} onClick={() => setRemove(pack.manifest.level.id)}>移除 {pack.manifest.level.id} 级资源</button>}
    </article>)}</div>
    {!packs.length && <p>尚未导入独立资源包。</p>}
    {usage?.quota !== undefined && <p className="resource-space">浏览器已用约 {size(usage.usage ?? 0)}，当前配额约 {size(usage.quota)}。</p>}
    {!android && <p className="resource-note">资源保存在此浏览器中。清除网站数据会删除已导入资源和本机记录，请保留资源包并备份学习记录。</p>}
    {!android && import.meta.env.PROD && import.meta.env.MODE !== 'bundled' && <p className="resource-note">{offlineReady ? '网页已可离线重新打开。' : '正在准备网页离线启动，请保持网络连接片刻。'}</p>}
    <p className="resource-note">导入期间请保持阅读器打开；空间不足或文件校验失败时，原有资源包继续保留。</p>
    {message && <p role={error ? 'alert' : 'status'} aria-live="polite">{message}</p>}
  </section>;
}
