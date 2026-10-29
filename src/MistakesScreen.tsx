import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { App as NativeApp } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import type { Library } from './types';
import { Feedback, PracticeScreen, QuestionText } from './PracticeScreen';
import { newMistakeSession, type MistakeItem, type PracticeBookRecord } from './practice';
import './practice.css';

interface Props {
  library: Library; items: MistakeItem[]; record: PracticeBookRecord; exitLabel?: string;
  onChange: (update: (record: PracticeBookRecord) => PracticeBookRecord) => void;
  onExit: () => void;
}
const date = (time: number) => new Date(time).toLocaleString('zh-CN');
export function MistakesScreen({ library, items, record, exitLabel = '返回书架', onChange, onExit }: Props) {
  const current = record.sessions.find(s => s.id === record.currentSessionId);
  const [practising, setPractising] = useState(!!current && current.completedAt === null);
  const [detailKey, setDetailKey] = useState<string | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const active = practising && !!current;
  const detail = items.find(i => i.key === detailKey);
  const available = items.filter(i => i.available);
  const back = useCallback(() => { if (detailKey) setDetailKey(null); else onExit(); }, [detailKey, onExit]);
  useEffect(() => {
    if (active) return;
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); back(); } };
    document.addEventListener('keydown', key);
    const native = Capacitor.isNativePlatform() ? NativeApp.addListener('backButton', back) : null;
    return () => { document.removeEventListener('keydown', key); void native?.then(listener => listener.remove()); };
  }, [active, back]);
  useLayoutEffect(() => {
    if (!active) { scroll.current?.scrollTo({ top: 0 }); heading.current?.focus({ preventScroll: true }); }
  }, [active, detailKey]);
  const start = () => {
    const session = newMistakeSession(available, library);
    onChange(old => ({ ...old, currentSessionId: session.id, sessions: [...old.sessions, session], updateNotice: false }));
    setPractising(true);
  };
  if (active) return <PracticeScreen kind="mistakes" library={library} record={record} onChange={onChange}
    onExit={() => setPractising(false)}/>;
  return <div className="practice-screen" ref={scroll}><div className="practice-shell">
    <header className="practice-header"><button className="practice-text-button" onClick={back}><ArrowLeft size={18}/>
      {detail ? '返回错题列表' : exitLabel}</button><span>错题练习</span><small>句子还原</small></header>
    {detail ? <section className="mistake-detail">
      <h1 ref={heading} tabIndex={-1}>{detail.question.blank.text}</h1>
      <p className="practice-note">{detail.bookTitle} · {detail.question.source.pageLabel}</p>
      <p className="practice-lead">{detail.needsPractice ? '还需复习' : '最近独立答对'}</p>
      {!detail.available && <p className="practice-note">此题内容已更新或不再提供，保留原作答记录，暂不参与练习。</p>}
      <dl className="mistake-stats">
        <div><dt>独立作答</dt><dd>{detail.independentCount} 次</dd></div>
        <div><dt>答错</dt><dd>{detail.wrongCount} 次</dd></div>
        <div><dt>错误率</dt><dd>{detail.errorRate === null ? '暂无' : `${Math.round(detail.errorRate * 100)}%`}</dd></div>
        <div><dt>求助</dt><dd>{detail.helpCount} 次</dd></div>
      </dl>
      <p className="practice-note">错误率 = 答错次数 ÷ 独立作答次数。求助和本轮重试单独记录，不计入错误率。最近答对不代表已经完全掌握。</p>
      <details className="mistake-answer"><summary>查看原句与讲解</summary>
        <QuestionText question={detail.question} revealed/><Feedback question={detail.question}/>
      </details>
      <h2>作答记录</h2>
      <p className="practice-note">本轮重试 {detail.retryCount} 次；未提交的选择不计入统计。</p>
      <ol className="mistake-attempts">{[...detail.history].reverse().map((a, index) => <li key={index}>
        <span>{date(a.submittedAt!)} · {a.retry ? '本轮重试' : '首次作答'}</span>
        <p>{a.helpUsed ? '不会，先看了讲解' : `选择：${a.options.find(o => o.id === a.selectedId)?.text} · ${a.selectedId === a.question.answerId ? '答对' : '答错'}`}</p>
      </li>)}</ol>
    </section> : <section className="practice-home">
      <h1 ref={heading} tabIndex={-1}>错题练习</h1>
      <p className="practice-lead">把之前答错或不会的句子，再独立还原一次。每轮最多五题。</p>
      {record.updateNotice && <p className="practice-note" role="status">练习内容已更新，请重新开始。旧作答记录仍可查看。</p>}
      <div className="practice-intro-card">
        <p>当前 {items.length} 道错题，{available.length} 道可练习。</p>
        <p>系统会自动安排题目，点开始就可以练习。每题保留原书的七个选项，提交后再看答案。</p>
        {current?.completedAt === null ? <>
          <p>还有一轮错题练习未完成，请先继续这一轮。</p>
          <button className="primary practice-primary" onClick={() => setPractising(true)}>继续错题练习<ArrowRight size={18}/></button>
        </> : <button className="primary practice-primary" disabled={!available.length} onClick={start}>开始错题练习<ArrowRight size={18}/></button>}
      </div>
      {!items.length && <p className="practice-note">还没有错题。完成读后练习后，答错或求助的题会自动收集到这里。</p>}
      <div className="mistake-list">{items.map(item => <button key={item.key} className="mistake-card" onClick={() => setDetailKey(item.key)}>
        <span className="mistake-word" lang="en">{item.question.blank.text}</span><span>{item.bookTitle} · {item.question.source.pageLabel}</span>
        <small>{item.needsPractice ? '还需复习' : '最近独立答对'}{!item.available && ' · 仅保留记录'}</small>
        <span className="mistake-card-action">查看记录<ArrowRight size={16}/></span>
      </button>)}</div>
    </section>}
  </div></div>;
}
