import { useLayoutEffect, useRef, useState } from 'react';
import type { LessonPage } from './types';
import { useResourceAsset } from './use-resource-asset';

export function OriginalPage({ page, focusedUnit, onSentence }: {
  page: LessonPage; focusedUnit: string | null;
  onSentence: (unitId: string) => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [loaded, setLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const pointer = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const paper = page.paper;
  const asset = useResourceAsset(paper.image);
  const fitWidth = Math.max(1, Math.min(size.width - 24, (size.height - 24) * paper.width / paper.height, 1100));
  const width = fitWidth, height = width * paper.height / paper.width;

  useLayoutEffect(() => {
    const element = viewport.current!;
    const updateSize = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return <section className="original-reader" data-page-id={page.id} aria-label={`${page.bookTitle}，${page.label}`}>
    <div className="paper-viewport" ref={viewport}>
      <div className="paper-stage" style={{ width: Math.max(size.width, width + 24), height: Math.max(size.height, height + 24) }}>
        <div className="paper-image" style={{ width, height }}>
          {asset.src && <img src={asset.src} alt={`${page.bookTitle} · ${page.label}原书页面`} width={paper.width} height={paper.height} draggable={false}
            onLoad={() => setLoaded(true)} onError={() => setImageError(true)}/>}
          {loaded && <svg className="sentence-hotspots" viewBox={`0 0 ${paper.width} ${paper.height}`}
            onPointerDown={event => { pointer.current = { x: event.clientX, y: event.clientY, moved: false }; }}
            onPointerMove={event => { if (pointer.current && Math.hypot(event.clientX - pointer.current.x, event.clientY - pointer.current.y) > 12) pointer.current.moved = true; }}
            onPointerCancel={() => { if (pointer.current) pointer.current.moved = true; }}>
            {paper.hotspots.map(spot => {
              const unit = page.sourceUnits.find(item => item.id === spot.unitId)!;
              return <g key={unit.id} role="button" tabIndex={0} aria-label={`讲解：${unit.text}`} data-unit-id={unit.id}
                className={focusedUnit === unit.id ? 'last-sentence' : ''}
                onClick={event => { if (event.detail === 0 || !pointer.current?.moved) onSentence(unit.id); }}
                onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSentence(unit.id); } }}>
                {spot.rects.map((rect, index) => <rect key={index} {...rect} rx={3}/>)}
              </g>;
            })}
          </svg>}
          {!loaded && <div className="page-image-status" role={imageError || asset.failed ? 'alert' : 'status'}>{imageError || asset.failed ? '这一页暂时打不开，请重新翻到这一页。' : '正在翻开这一页…'}</div>}
        </div>
      </div>
    </div>
  </section>;
}
