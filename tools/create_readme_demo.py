"""Create original demonstration books solely for documentation screenshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont

from build_resource_packs import build_pack
from practice_content import compile_practice
from synthetic_content import SENTENCES, synthetic_book

ROOT = Path(__file__).resolve().parents[1]


def font(size: int, bold: bool = False):
    candidates = [
        Path('C:/Windows/Fonts') / ('arialbd.ttf' if bold else 'arial.ttf'),
        Path('/usr/share/fonts/truetype/dejavu') / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def robot(draw: ImageDraw.ImageDraw, x: int, y: int, color: str):
    draw.line((x + 90, y + 10, x + 90, y + 48), fill='#29423d', width=6)
    draw.ellipse((x + 78, y, x + 102, y + 24), fill='#e6b34f')
    draw.rounded_rectangle((x, y + 45, x + 180, y + 155), radius=26, fill=color, outline='#29423d', width=5)
    for eye in (x + 45, x + 115):
        draw.ellipse((eye, y + 80, eye + 20, y + 100), fill='#29423d')
    draw.arc((x + 65, y + 100, x + 115, y + 132), 0, 180, fill='#29423d', width=4)
    draw.rounded_rectangle((x + 25, y + 170, x + 155, y + 275), radius=20, fill=color, outline='#29423d', width=5)
    for wheel in (x + 40, x + 115):
        draw.ellipse((wheel, y + 260, wheel + 30, y + 300), fill='#29423d')
    draw.line((x + 25, y + 200, x - 25, y + 235), fill='#29423d', width=10)
    draw.line((x + 155, y + 200, x + 220, y + 190), fill='#29423d', width=10)


def main() -> None:
    content = ROOT / 'artifacts' / 'readme-demo' / 'content'
    content.mkdir(parents=True, exist_ok=True)
    books = []
    styles = [
        ('The Garden Robot', '#e9f2e8', '#94c1a0'),
        ('A Lantern for Milo', '#fff0d3', '#e7b567'),
        ('The Paper Moon', '#e9edf9', '#a6b6db'),
    ]
    for number, (title, background, accent) in enumerate(styles, 3):
        book, pages, authoring = synthetic_book('Q', number)
        book.update(title=title, subtitle='原创演示 · 看一看，读一读，练一练', category='原创演示')
        for i, page in enumerate(pages.values(), 1):
            page.update(bookTitle=title, title=title)
            sentence = page['sourceUnits'][0]['text']
            page['blocks'][0]['title'] = '一起读懂这句话'
            if i == 5:
                page['blocks'][0]['markdown'] = (
                    '**中文：** 米洛把一盏明亮的灯笼放在大门旁边。\n\n**读懂这句：**\n\n'
                    '`bright` 在这里是“明亮的”，修饰 `lantern`（灯笼）。\n\n'
                    '`beside the gate` 表示“在大门旁边”，告诉我们灯笼放在哪里。\n\n'
                    '把画面连起来：米洛拿来灯笼，再把它放到大门旁边。')
            page['sha256'] = hashlib.sha256(json.dumps(page['blocks'], ensure_ascii=False).encode()).hexdigest()
            page['paper'].update(width=800, height=1100)
            page['paper']['hotspots'][0]['rects'] = [{'x': 58, 'y': 195, 'width': 690, 'height': 170}]
            image = Image.new('RGB', (800, 1100), '#fffdf7')
            draw = ImageDraw.Draw(image)
            draw.text((60, 55), title, font=font(29, True), fill='#2c4a40')
            draw.line((60, 125, 740, 125), fill=accent, width=3)
            for row, line in enumerate(textwrap.wrap(sentence, width=29)):
                draw.text((60, 210 + row * 63), line, font=font(43), fill='#203b32')
            draw.rounded_rectangle((60, 450, 740, 930), radius=32, fill=background)
            draw.ellipse((105, 820, 705, 900), fill='#d2dfcb')
            robot(draw, 270, 510, accent)
            draw.text((60, 1010), 'ORIGINAL DEMO / NOT RAZ MATERIAL', font=font(19), fill='#64766b')
            draw.text((710, 1000), str(i), font=font(27), fill='#2c4a40')
            target = content.parent / page['paper']['image']
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, 'WEBP', quality=90)
        cover = Image.new('RGB', (600, 800), background)
        draw = ImageDraw.Draw(cover)
        draw.text((45, 40), 'LITTLE READING STORIES', font=font(19, True), fill='#4c6b5c')
        for row, line in enumerate(textwrap.wrap(title, width=15)):
            draw.text((45, 110 + row * 77), line, font=font(58, True), fill='#203b32')
        robot(draw, 200, 350, accent)
        draw.text((45, 720), 'ORIGINAL DEMO', font=font(21, True), fill='#4c6b5c')
        cover.save(content.parent / book['cover'], 'WEBP', quality=90)
        audio = content.parent / next(iter(pages.values()))['audio'][0]['src']
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes((bytes.fromhex('fffb9000') + bytes(413)) * 400)
        for item in authoring['questions'] + authoring['distractors']:
            item['source']['lessonSha256'] = pages[item['source']['pageId']]['sha256']
        authoring['questions'][0].update(meaning='明亮的', translation='米洛把一盏明亮的灯笼放在大门旁边。',
                                        explanation='bright 描写灯笼的光线很明亮；把它放回原句，读一读是否通顺。')
        practice = (content.parent / book['contentPath']).parent / '_source' / 'practice'
        practice.mkdir(parents=True, exist_ok=True)
        (practice / 'exercises.json').write_text(json.dumps(authoring), encoding='utf-8')
        book['practice'] = compile_practice(book['id'], practice, pages)
        (content.parent / book['contentPath']).write_text(json.dumps({
            'schemaVersion': 'raz-reader-book/1.0', 'contentVersion': 'readme-demo-v1', **book, 'pages': pages,
        }, ensure_ascii=False), encoding='utf-8')
        books.append(book)
    (content / 'manifest.json').write_text(json.dumps({
        'schemaVersion': 'raz-reader-content/2.0', 'bundleId': 'raz-local-preview',
        'contentVersion': 'readme-demo-v1', 'contentStatus': 'draft-preview',
        'levels': [{'id': 'Q', 'label': 'Q 级', 'bookIds': [b['id'] for b in books]}],
        'books': books, 'pageCount': len(books) * len(SENTENCES),
    }, ensure_ascii=False), encoding='utf-8')
    result = build_pack(content, content.parent, 'Q')
    (content.parent / 'pack.json').write_text(json.dumps(result), encoding='utf-8')
    print('Created original screenshot demo:', result['path'])


if __name__ == '__main__':
    main()
