import { useState } from 'react';
import type { Book } from './types';
import { useResourceAsset } from './use-resource-asset';

export function BookCover({ book, small = false, decorative = false }: {
  book: Book; small?: boolean; decorative?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const asset = useResourceAsset(book.cover);
  return <div className={`book-cover ${small ? 'book-cover-small' : ''}`}>
    {!failed && !asset.failed && asset.src && (
      <img
        src={asset.src}
        alt={decorative ? '' : `${book.title} 封面`}
        draggable={false}
        onLoad={event => {
          const image = event.currentTarget;
          image.dataset.fit = image.naturalWidth / image.naturalHeight > 644 / 1000 ? 'wide' : 'tall';
        }}
        onError={() => setFailed(true)}
      />
    )}
    {(failed || asset.failed) && <span className="cover-fallback" aria-hidden={decorative}>
      <small>RAZ · LEVEL {book.level}</small><b>{book.title}</b>
    </span>}
  </div>;
}
