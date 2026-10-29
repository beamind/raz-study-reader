"""Original test fixtures. No text, images, or recordings are copied from RAZ.

The level letters and book ID prefixes exercise the existing pack protocol.
The generated audio is MPEG silence for playback tests, not a spoken recording.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from practice_content import compile_practice

SENTENCES = [
    ('The garden robot checks the gate each morning.', 'robot'),
    ('Milo keeps a miniature wagon under his desk.', 'miniature'),
    ('We can conceive a new design for the lantern.', 'conceive'),
    ('A paper moon hangs above the kitchen table.', 'paper'),
    ('Milo puts a bright lantern beside the gate.', 'bright'),
    ('The robot carries a basket along the path.', 'carries'),
    ('Nora measured the ribbon before tying a bow.', 'measured'),
    ('The green wagon moves slowly past the fountain.', 'slowly'),
    ('We gather near the gate before sunset.', 'gather'),
]


def synthetic_book(level: str = 'P', number: int = 1) -> tuple[dict, dict, dict]:
    book_id = f'raz-{level.lower()}-{number:03}'
    title = 'The Garden Robot' if level == 'Q' else 'A Lantern for Milo'
    base = f'content/levels/{level}/books/{number:03}-synthetic/'
    pages, questions, distractors = {}, [], []
    for i, (sentence, word) in enumerate(SENTENCES, 1):
        page_id = f'{book_id}-p{i:03}'
        unit_id, block_id = f'{page_id}-u02', f'{page_id}-s01'
        digest = hashlib.sha256(f'original-fixture:{page_id}:{sentence}'.encode()).hexdigest()
        pages[page_id] = {
            'id': page_id, 'bookId': book_id, 'bookTitle': title, 'level': level,
            'pdfPage': i, 'paperLabel': str(i), 'label': str(i), 'title': title,
            'revision': 1, 'status': 'draft', 'sha256': digest,
            'sourceUnits': [{'id': unit_id, 'kind': 'sentence', 'text': sentence}],
            'blocks': [{'id': block_id, 'kind': 'sentence', 'title': '原创测试句',
                        'sourceRefs': [unit_id], 'original': sentence,
                        'markdown': f'**中文：** 这是用于验证阅读器的原创例句。\n\n**读懂这句：**\n\n`{word}` 是本页练习词。'}],
            'paper': {'image': base + f'pages/p{i:03}/page.webp', 'width': 600, 'height': 400,
                      'hotspots': [{'unitId': unit_id, 'rects': [{'x': 30, 'y': 90, 'width': 540, 'height': 65}]}]},
            'audio': [{'src': base + 'pages/p001/audio/silence.mp3', 'durationMs': 10448,
                       'clips': [{'unitId': unit_id, 'startMs': 0, 'endMs': 9000}]}],
        }
        source = {'text': sentence, 'pageId': page_id, 'unitId': unit_id, 'blockId': block_id,
                  'lessonSha256': digest, 'start': 0, 'end': len(sentence)}
        start = sentence.index(word)
        span = {'start': start, 'end': start + len(word), 'text': word}
        if i >= 5:
            questions.append({'id': f'{book_id}-q{i-4:02}', 'revision': 1,
                              'source': source, 'blank': span, 'meaning': '原创练习词',
                              'translation': '这是用于软件测试的原创例句。',
                              'explanation': f'在原句中找到 `{word}`，核对它的拼写和位置。'})
        elif i in (2, 3):
            distractors.append({'id': f'{book_id}-d{i-1:02}', 'source': source,
                                'expression': span, 'meaning': '本书其他原创例句中的词'})
    authoring = {'schemaVersion': 'raz-restoration-authoring/1.0', 'bookId': book_id,
                 'status': 'ready', 'questions': questions, 'distractors': distractors}
    book = {'id': book_id, 'title': title, 'subtitle': '原创软件测试样例，非 RAZ 教材',
            'category': '原创测试', 'level': level, 'cover': base + 'cover.webp',
            'contentPath': base + 'book.json', 'pageIds': list(pages)}
    return book, pages, authoring


def write_fixture_content(content: Path) -> dict:
    from PIL import Image, ImageDraw
    books = []
    for level, number in [('Q', 3), ('P', 1)]:
        book, pages, authoring = synthetic_book(level, number)
        book_root = content.parent / book['contentPath']
        book_root.parent.mkdir(parents=True, exist_ok=True)
        # Authoring input exists only under ignored generated fixture output.
        practice = book_root.parent / '_source' / 'practice'
        practice.mkdir(parents=True, exist_ok=True)
        (practice / 'exercises.json').write_text(json.dumps(authoring), encoding='utf-8')
        book['practice'] = compile_practice(book['id'], practice, pages)
        for image_path in [book['cover'], *(page['paper']['image'] for page in pages.values())]:
            target = content.parent / image_path
            target.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new('RGB', (600, 400), '#edf5e9')
            draw = ImageDraw.Draw(image)
            draw.text((30, 30), book['title'], fill='#254734')
            page = next((p for p in pages.values() if p['paper']['image'] == image_path), None)
            if page:
                draw.text((30, 110), page['sourceUnits'][0]['text'], fill='#152b20')
            image.save(target, 'WEBP')
        audio = content.parent / next(iter(pages.values()))['audio'][0]['src']
        audio.parent.mkdir(parents=True, exist_ok=True)
        # 400 MPEG-1 Layer III frames, 128 kbit/s, 44.1 kHz, silent main data.
        audio.write_bytes((bytes.fromhex('fffb9000') + bytes(413)) * 400)
        runtime = {'schemaVersion': 'raz-reader-book/1.0', 'contentVersion': 'synthetic-v1',
                   **book, 'pages': pages}
        book_root.write_text(json.dumps(runtime, ensure_ascii=False), encoding='utf-8')
        books.append(book)
    library = {'schemaVersion': 'raz-reader-content/2.0', 'bundleId': 'raz-local-preview',
               'contentVersion': 'synthetic-v1', 'contentStatus': 'draft-preview',
               'levels': [{'id': level, 'label': f'{level} 级',
                           'bookIds': [b['id'] for b in books if b['level'] == level]} for level in ['Q', 'P']],
               'books': books, 'pageCount': sum(len(b['pageIds']) for b in books)}
    (content / 'manifest.json').write_text(json.dumps(library, ensure_ascii=False), encoding='utf-8')
    return library
