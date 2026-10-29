"""Extract word positions locally for the original-page reader (build time only)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jobs', type=Path)
    parser.add_argument('--text-score', type=float, default=.5)
    parser.add_argument('--box-thresh', type=float, default=.5)
    parser.add_argument('--model-type', choices=('mobile', 'server'), default='mobile')
    parser.add_argument('--ocr-version', choices=('4', '5'), default='4')
    parser.add_argument('--horizontal-tiles', type=int, default=1)
    args = parser.parse_args()
    import numpy as np
    from PIL import Image
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    engine = RapidOCR(params={
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        'Rec.lang_type': LangRec.EN,
        'Rec.model_type': ModelType(args.model_type),
        'Rec.ocr_version': OCRVersion.PPOCRV4 if args.ocr_version == '4' else OCRVersion.PPOCRV5,
        'Det.limit_side_len': 2000,
        'Global.log_level': 'warning',
    })
    for job in json.loads(args.jobs.read_text(encoding='utf-8')):
        words = []
        with Image.open(job['image']) as original:
            image = original.convert('RGB')
            width, height = image.size
            for tile_index in range(args.horizontal_tiles):
                tile_left = round(width * tile_index / args.horizontal_tiles)
                tile_right = round(width * (tile_index + 1) / args.horizontal_tiles)
                tile = np.asarray(image.crop((tile_left, 0, tile_right, height)))
                result = engine(tile, return_word_box=True,
                                text_score=args.text_score, box_thresh=args.box_thresh)
                for line_index, line in enumerate(result.word_results or []):
                    for text, score, box in line:
                        left, top = min(p[0] for p in box), min(p[1] for p in box)
                        right, bottom = max(p[0] for p in box), max(p[1] for p in box)
                        words.append({'text': text, 'line': tile_index * 10000 + line_index,
                                      'score': float(score), 'x': int(left) + tile_left,
                                      'y': int(top), 'width': int(right - left),
                                      'height': int(bottom - top)})
        output = Path(job['output'])
        temporary = output.with_suffix('.tmp')
        engine_name = (f'rapidocr-3.9.2-ppocrv{args.ocr_version}-en-{args.model_type}-'
                       f'text{args.text_score:g}-box{args.box_thresh:g}-tiles{args.horizontal_tiles}')
        temporary.write_text(json.dumps({'width': width, 'height': height, 'engine': engine_name,
                                         'words': words}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(output)
        print(f"Located {len(words)} words: {job['id']}", flush=True)


if __name__ == '__main__':
    main()
