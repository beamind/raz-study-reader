import { useEffect, useState } from 'react';
import { openResourceAsset } from './resource-packs';

export function useResourceAsset(path: string): { src?: string; failed: boolean } {
  const [asset, setAsset] = useState<{ path: string; src?: string; failed: boolean }>({ path, failed: false });
  useEffect(() => {
    if (!path.startsWith('razpack:')) return;
    let active = true, release: (() => void) | undefined;
    void openResourceAsset(path).then(lease => {
      if (!active) { lease.release(); return; }
      release = lease.release; setAsset({ path, src: lease.url, failed: false });
    }).catch(() => { if (active) setAsset({ path, failed: true }); });
    return () => { active = false; release?.(); };
  }, [path]);
  if (!path.startsWith('razpack:')) return { src: path, failed: false };
  return asset.path === path ? asset : { failed: false };
}
