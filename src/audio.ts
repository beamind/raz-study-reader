import { useCallback, useEffect, useRef, useState } from 'react';
import type { LessonPage } from './types';
import { openResourceAsset, type AssetLease } from './resource-packs';

export interface SentenceClip { src: string; startMs: number; endMs: number }
export type ClipStatus = 'idle' | 'loading' | 'playing' | 'error';

export function findClip(page: LessonPage, unitId: string): SentenceClip | null {
  for (const track of page.audio) {
    const clip = track.clips.find(item => item.unitId === unitId);
    if (clip) return { src: track.src, startMs: clip.startMs, endMs: clip.endMs };
  }
  return null;
}

/** Play one sentence out of the page recording, stopping at the end of its range. */
export function useClipPlayer() {
  const [status, setStatus] = useState<ClipStatus>('idle');
  const element = useRef<HTMLAudioElement | null>(null);
  const frame = useRef(0);
  const request = useRef(0);
  const source = useRef<{ path: string; lease: AssetLease } | null>(null);

  const stop = useCallback(() => {
    request.current += 1;
    cancelAnimationFrame(frame.current);
    element.current?.pause();
    setStatus('idle');
  }, []);

  useEffect(() => () => {
    stop();
    element.current?.removeAttribute('src');
    element.current?.load();
    source.current?.lease.release(); source.current = null;
  }, [stop]);

  const play = useCallback(async (clip: SentenceClip) => {
    request.current += 1;
    const token = request.current;
    cancelAnimationFrame(frame.current);
    const audio = element.current ??= new Audio();
    audio.pause();
    setStatus('loading');
    try {
      if (source.current?.path !== clip.src) {
        const lease = await openResourceAsset(clip.src);
        if (token !== request.current) { lease.release(); return; }
        const previous = source.current;
        audio.src = lease.url;
        source.current = { path: clip.src, lease };
        previous?.lease.release();
      }
      if (audio.readyState < HTMLMediaElement.HAVE_METADATA) {
        await new Promise<void>((resolve, reject) => {
          audio.addEventListener('loadedmetadata', () => resolve(), { once: true });
          audio.addEventListener('error', () => reject(new Error('page audio failed to load')), { once: true });
        });
      }
      if (token !== request.current) return;
      audio.currentTime = clip.startMs / 1000;
      await audio.play();
      if (token !== request.current) return;
      setStatus('playing');
    } catch {
      if (token === request.current) setStatus('error');
      return;
    }
    const watch = () => {
      if (token !== request.current) return;
      if (audio.ended || audio.currentTime * 1000 >= clip.endMs) {
        audio.pause();
        setStatus('idle');
        return;
      }
      frame.current = requestAnimationFrame(watch);
    };
    frame.current = requestAnimationFrame(watch);
  }, []);

  return { status, play, stop };
}
