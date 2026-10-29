"""Compile book sentences into a small, shared-word-bank restoration activity."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_source(src: dict, pages: dict, book_id: str, prefix: str) -> dict:
    require(isinstance(src, dict), f'Invalid source: {prefix}')
    page = pages.get(src.get('pageId'))
    require(page is not None and page['bookId'] == book_id, f'Source must belong to this book: {prefix}')
    require(src.get('lessonSha256') == page['sha256'], f'Lesson changed: {prefix}')
    unit = next((u for u in page['sourceUnits'] if u['id'] == src.get('unitId')), None)
    block = next((b for b in page['blocks'] if b['id'] == src.get('blockId')), None)
    require(unit is not None and block is not None and unit['id'] in block['sourceRefs'],
            f'Invalid source unit/block: {prefix}')
    start, end = src.get('start'), src.get('end')
    require(type(start) is int and type(end) is int and 0 <= start < end <= len(unit['text']),
            f'Invalid source span: {prefix}')
    require(nonempty(src.get('text')) and src['text'] == unit['text'][start:end],
            f'Source quote mismatch: {prefix}')
    return page


def validate_expression(span: dict, text: str, prefix: str) -> str:
    require(isinstance(span, dict), f'Invalid blank span: {prefix}')
    start, end = span.get('start'), span.get('end')
    require(type(start) is int and type(end) is int and 0 <= start < end <= len(text),
            f'Invalid blank span: {prefix}')
    word = span.get('text')
    require(nonempty(word) and word == word.strip() and text[start:end] == word,
            f'Blank does not restore source: {prefix}')
    require((start == 0 or not text[start - 1].isalnum()) and
            (end == len(text) or not text[end].isalnum()), f'Blank splits a word: {prefix}')
    return word


def compile_practice(book_id: str, folder: Path, pages: dict) -> dict | None:
    path = folder / 'exercises.json'
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    require(data.get('schemaVersion') == 'raz-restoration-authoring/1.0', 'Invalid restoration schema')
    require(data.get('bookId') == book_id, 'Practice book ID mismatch')
    require(data.get('status') in ('draft', 'ready'), 'Invalid practice status')
    questions = data.get('questions')
    require(isinstance(questions, list) and len(questions) == 5,
            'A restoration set needs exactly five questions')
    distractors = data.get('distractors')
    require(isinstance(distractors, list) and len(distractors) == 2,
            'A restoration set needs exactly two same-book distractors')
    ids, words, output = set(), set(), []
    for q in questions:
        require(isinstance(q, dict) and nonempty(q.get('id')), 'Missing question ID')
        prefix = f'{book_id}/{q["id"]}'
        require(q['id'] not in ids, f'Duplicate question ID: {prefix}')
        ids.add(q['id'])
        require(type(q.get('revision')) is int and q['revision'] > 0, f'Invalid revision: {prefix}')
        src = q.get('source', {})
        page = validate_source(src, pages, book_id, prefix)
        blank = q.get('blank', {})
        word = validate_expression(blank, src['text'], prefix)
        require(word.casefold() not in words, f'Duplicate bank word: {prefix}')
        words.add(word.casefold())
        for key in ('meaning', 'translation', 'explanation'):
            require(nonempty(q.get(key)), f'Missing {key}: {prefix}')
        output.append({
            'id': q['id'], 'revision': q['revision'], 'answerId': q['id'], 'blank': blank,
            **{key: q[key] for key in ('meaning', 'translation', 'explanation')},
            'source': {**{key: src[key] for key in ('text', 'pageId', 'unitId', 'blockId', 'lessonSha256')},
                       'pageLabel': page['label']},
        })
    # Keep the complete bank for retries and delayed review, even for a single question.
    options = [{'id': q['id'], 'text': q['blank']['text']} for q in output]
    for d in distractors:
        require(isinstance(d, dict) and nonempty(d.get('id')), 'Missing distractor ID')
        prefix = f'{book_id}/{d["id"]}'
        require(d['id'] not in ids, f'Duplicate option ID: {prefix}')
        ids.add(d['id'])
        src = d.get('source', {})
        validate_source(src, pages, book_id, prefix)
        word = validate_expression(d.get('expression', {}), src['text'], prefix)
        require(word.casefold() not in words, f'Duplicate bank word: {prefix}')
        words.add(word.casefold())
        require(nonempty(d.get('meaning')), f'Missing meaning: {prefix}')
        options.append({'id': d['id'], 'text': word})
    if data['status'] != 'ready':
        return None
    pack = {'schemaVersion': 'raz-restoration/1.0', 'bookId': book_id,
            'options': options, 'questions': output}
    version = hashlib.sha256(json.dumps(pack, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()[:20]
    return {**pack, 'version': version}
