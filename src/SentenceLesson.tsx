import type { ReactNode } from 'react';
import { Leaf, LoaderCircle, Square, Volume2 } from 'lucide-react';
import { findClip, useClipPlayer } from './audio';
import { SafeMarkdown } from './Markdown';
import type { LessonBlock, LessonPage, SourceUnit } from './types';

function Body({ block, text = block.markdown, part = 'body' }: { block: LessonBlock; text?: string; part?: string }) {
  return <div className="markdown" data-selectable="lesson" data-selection-part={part} data-source-refs={block.sourceRefs.join(' ')}><SafeMarkdown text={text}/></div>;
}

function focusedBody(block: LessonBlock, unit: SourceUnit) {
  const paragraphs = block.markdown.trim().split(/\n\s*\n/);
  // The focused original is already shown above this explanation.
  if (paragraphs[0]?.replace(/^\*\*原文：\*\*\s*/, '') === unit.text && paragraphs[0].startsWith('**原文：**')) {
    paragraphs.shift();
  }
  return paragraphs.join('\n\n').replace(/^\*\*意思：\*\*\s*/, '');
}

function ReadAloud({ page, unit }: { page: LessonPage; unit: SourceUnit }) {
  const { status, play, stop } = useClipPlayer();
  const clip = findClip(page, unit.id);
  if (!clip) return null;
  const sounding = status === 'loading' || status === 'playing';
  return <button className={`read-aloud ${sounding ? 'sounding' : ''}`} aria-pressed={sounding}
      aria-label={sounding ? `停止播放原声：${unit.text}` : `${status === 'error' ? '重新播放' : '播放'}原声：${unit.text}`}
      onClick={() => sounding ? stop() : void play(clip)}>
    {status === 'loading' ? <LoaderCircle size={18} className="spinning"/> : sounding ? <Square size={15}/> : <Volume2 size={18}/>}
  </button>;
}

function termPattern(term: string) {
  let pattern = '';
  for (const character of term) {
    if (character === "'" || character === '’') pattern += "['’]";
    else if (/\s/u.test(character)) pattern += '\\s+';
    else pattern += character.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  return `(?<![A-Za-z0-9])${pattern}(?![A-Za-z0-9])`;
}

function highlightDetailTerms(text: string, detail: string): ReactNode {
  const terms = [...detail.matchAll(/`([^`\n]+)`/g)]
    .map(match => match[1].replace(/(?:\.{3}|…)$/u, '').trim())
    .filter(Boolean)
    .filter((term, index, all) => all.findIndex(candidate => candidate.toLocaleLowerCase() === term.toLocaleLowerCase()) === index)
    .sort((a, b) => b.length - a.length);
  if (!terms.length) return text;
  const matcher = new RegExp(terms.map(termPattern).join('|'), 'giu');
  const result: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(matcher)) {
    const index = match.index;
    if (index > cursor) result.push(text.slice(cursor, index));
    result.push(<mark className="term-highlight" key={`${index}:${match[0]}`}>{match[0]}</mark>);
    cursor = index + match[0].length;
  }
  if (!result.length) return text;
  if (cursor < text.length) result.push(text.slice(cursor));
  return result;
}

export function SentenceLesson({ page, unitId }: { page: LessonPage; unitId: string }) {
  const unit = page.sourceUnits.find(item => item.id === unitId)!;
  const related = page.blocks.filter(block => block.sourceRefs.includes(unitId));
  const sentence = related.find(block => block.kind === 'sentence');
  const other = related.filter(block => block.kind === 'vocabulary' || block.kind === 'structure');
  const fallback = related.filter(block => block.kind !== 'question' && block.kind !== 'answer');
  const marker = '**读懂这句：**';
  const split = sentence?.markdown.indexOf(marker) ?? -1;
  const meaningMarker = '**意思：**';
  const rawMeaning = sentence ? (split < 0 ? sentence.markdown : sentence.markdown.slice(0, split)).trim() : '';
  const meaning = rawMeaning.startsWith(meaningMarker) ? rawMeaning.slice(meaningMarker.length).trim() : rawMeaning;
  const detail = sentence && split >= 0 ? sentence.markdown.slice(split + marker.length).trim() : '';

  return <div className="sentence-lesson" data-focused-unit={unitId}>
    <section className="focused-sentence" data-block-id={sentence?.id ?? related[0]?.id}>
      <p className="eyebrow">{page.label} <span>·</span> 读懂这一句</p>
      <div className="focused-original">
        <p className="original" lang="en" data-selectable="source" data-source-refs={unitId}>{highlightDetailTerms(unit.text, detail)}</p>
        <ReadAloud page={page} unit={unit}/>
      </div>
      {sentence && <div className="sentence-meaning"><Body block={sentence} text={meaning} part="meaning"/></div>}
      {detail && sentence && <section className="sentence-detail"><Body block={sentence} text={detail} part="detail"/></section>}
    </section>
    {sentence ? other.length > 0 && <div className="related-words"><p className="related-label">这句话里的词语和用法</p>{other.map(block =>
      <section className="related-word" key={block.id} data-block-id={block.id}>
        <h3 className="related-word-title"><Leaf size={17}/><span data-selectable="lesson" data-selection-part="title" data-source-refs={block.sourceRefs.join(' ')}>{block.title}</span></h3><Body block={block}/>
      </section>)}</div> : fallback.map(block => <section className="fallback-explanation" key={block.id} data-block-id={block.id}><Body block={block} text={focusedBody(block, unit)}/></section>)}
  </div>;
}
