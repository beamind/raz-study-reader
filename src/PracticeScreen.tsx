import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { App as NativeApp } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { ArrowLeft, ArrowRight, BookOpen, Check } from 'lucide-react';
import type { Book, Library } from './types';
import { SentenceLesson } from './SentenceLesson';
import { useContent } from './content';
import { advanceSession, isCorrect, newSession, newReviewSession, questionIdentity, selectAnswer, submitAnswer,
  type ReviewItem, type PracticeAttempt, type PracticeBookRecord, type PracticeQuestion, type PracticeSession } from './practice';
import './practice.css';

interface Props {
  book?: Book; library: Library; record: PracticeBookRecord; reviewItems?: ReviewItem[];
  kind?: 'mistakes'; onMistakes?: () => void;
  onChange: (update: (record: PracticeBookRecord) => PracticeBookRecord) => void; onExit: () => void;
}
export function QuestionText({ question, revealed = false }: { question: PracticeQuestion; revealed?: boolean }) {
  const chars = Array.from(question.source.text);
  return <p className="practice-sentence" lang="en">{chars.slice(0, question.blank.start).join('')}
    <span className={revealed ? 'practice-answer-word' : 'practice-blank'} aria-label={revealed ? undefined : '空格'}>
      {revealed ? question.blank.text : '________'}
    </span>{chars.slice(question.blank.end).join('')}</p>;
}
export function Feedback({ question, onReview }: { question: PracticeQuestion; onReview?: () => void }) {
  return <section className="practice-feedback" aria-label="作答反馈">
    <div className="practice-meaning"><span className="practice-label">这个表达在这里的意思</span>
      <p><b lang="en">{question.blank.text}</b><span>{question.meaning}</span></p></div>
    <p className="practice-explanation">{question.explanation}</p>
    <details className="practice-translation"><summary>看看整句意思</summary><p>{question.translation}</p></details>
    {onReview && <button className="practice-text-button" onClick={onReview}><BookOpen size={17}/>回看书中讲解<ArrowRight size={15}/></button>}
    <p className="practice-source">本书原句 · {question.source.pageLabel}</p>
  </section>;
}
export function PracticeScreen({ book, library, record, reviewItems, kind, onMistakes, onChange, onExit }: Props) {
  const { loadBook, retainBooks } = useContent();
  const [lessonError, setLessonError] = useState(false);
  const [lessonAttempt, setLessonAttempt] = useState(0);
  const [preparedSession, setPreparedSession] = useState<string | null>(null);
  const mistakes = kind === 'mistakes';
  const modeLabel = mistakes ? '错题练习' : '隔天复习';
  const [reviewQuestion, setReviewQuestion] = useState<PracticeQuestion | null>(null);
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [returnedQuestion, setReturnedQuestion] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null), scroll = useRef<HTMLDivElement>(null);
  const current = record.sessions.find(s => s.id === record.currentSessionId);
  const session = historyId ? record.sessions.find(s => s.id === historyId) : current;
  const summary = !!session && (session.completedAt !== null || !!historyId);
  const missingBooks = session ? [...new Set(session.attempts.filter(a => !library.books.some(b => b.id === a.bookId)).map(a => a.bookTitle))] : [];
  // Load only this round's books before exposing answers, so back-to-lesson works
  // after the device goes offline midway through a review or mistakes round.
  useEffect(() => {
    if (!session || summary || missingBooks.length) return;
    let active = true;
    const bookIds = [...new Set(session.attempts.map(a => a.bookId))];
    const release = retainBooks(bookIds);
    void Promise.allSettled(bookIds.map(loadBook))
      .then(() => { if (active) setPreparedSession(session.id); });
    return () => { active = false; release(); };
  }, [session?.id, summary, missingBooks.length, loadBook, retainBooks]);
  const attempt = !summary ? session?.attempts[session.index] : undefined;
  const question = attempt?.question;
  const questionKey = session && attempt ? `${session.id}:${session.index}` : null;
  const showAnswer = !!attempt && attempt.submittedAt !== null && returnedQuestion !== questionKey;
  const initial = session?.attempts.filter(a => !a.retry) ?? [];
  const retries = session?.attempts.filter(a => a.retry) ?? [];
  const canReview = (q: PracticeQuestion) => {
    const page = library.pages[q.source.pageId];
    if (page) return page.sha256 === q.source.lessonSha256 && page.sourceUnits.some(u => u.id === q.source.unitId);
    return library.books.some(b => b.practice?.questions.some(current =>
      current.source.pageId === q.source.pageId && current.source.lessonSha256 === q.source.lessonSha256 &&
      current.source.unitId === q.source.unitId));
  };
  useEffect(() => {
    if (!reviewQuestion) return;
    let active = true;
    setLessonError(false);
    const owner = library.books.find(b => b.pageIds.includes(reviewQuestion.source.pageId));
    if (!owner) { setLessonError(true); return; }
    const release = retainBooks([owner.id]);
    void loadBook(owner.id).catch(() => { if (active) setLessonError(true); });
    return () => { active = false; release(); };
  }, [reviewQuestion, lessonAttempt, loadBook, retainBooks]);
  const feedback = (q: PracticeQuestion) => <Feedback question={q}
    onReview={canReview(q) ? () => setReviewQuestion(q) : undefined}/>;
  const back = useCallback(() => {
    if (reviewQuestion) setReviewQuestion(null);
    else if (showAnswer) setReturnedQuestion(questionKey);
    else if (historyId) setHistoryId(null);
    else onExit();
  }, [reviewQuestion, showAnswer, questionKey, historyId, onExit]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); back(); } };
    document.addEventListener('keydown', key);
    const native = Capacitor.isNativePlatform() ? NativeApp.addListener('backButton', back) : null;
    return () => { document.removeEventListener('keydown', key); void native?.then(listener => listener.remove()); };
  }, [back]);
  useLayoutEffect(() => {
    scroll.current?.scrollTo({ top: 0, behavior: 'instant' });
    (heading.current ?? scroll.current?.querySelector<HTMLElement>('h1'))?.focus({ preventScroll: true });
  }, [session?.id, session?.index, summary, reviewQuestion?.id, historyId, showAnswer, preparedSession]);

  const start = (fresh: PracticeSession) => {
    setHistoryId(null); setReviewQuestion(null); setReturnedQuestion(null);
    onChange(old => ({ ...old, sessions: [...old.sessions, fresh], currentSessionId: fresh.id, updateNotice: false }));
  };
  const update = (fn: (s: PracticeSession) => PracticeSession) => {
    if (!session || summary) return;
    onChange(old => ({ ...old, sessions: old.sessions.map(s => s.id === session.id ? fn(s) : s) }));
  };
  const home = () => { setHistoryId(null); onChange(old => ({ ...old, currentSessionId: null })); };
  const selectedWord = (a: PracticeAttempt) => a.helpUsed ? '先看了讲解' : a.options.find(o => o.id === a.selectedId)?.text ?? '未作答';
  const reviewPage = reviewQuestion && canReview(reviewQuestion) ? library.pages[reviewQuestion.source.pageId] : null;
  const countLabel = session && attempt ? attempt.retry
    ? `再试 ${session.index - initial.length + 1} / ${retries.length}`
    : `${session.index + 1} / ${initial.length}` : '';
  const history = !!record.sessions.length && <details className="practice-history"><summary>练习记录（{record.sessions.length} 轮）</summary>
    {[...record.sessions].reverse().map(s => <button key={s.id} onClick={() => setHistoryId(s.id)}>
      <span>{s.mode === 'book' ? '本书还原' : s.mode === 'mistakes' ? '错题练习' : '隔天复习'} · {new Date(s.startedAt).toLocaleDateString('zh-CN')}</span>
      <small>{s.completedAt !== null ? '已完成' : '未完成'}</small><ArrowRight size={16}/></button>)}</details>;

  return <div className="practice-screen" ref={scroll}><div className="practice-shell">
    <header className="practice-header"><button className="practice-text-button" onClick={back}><ArrowLeft size={18}/>
      {reviewQuestion ? '返回练习' : showAnswer ? '返回题目' : historyId ? '返回本轮' : book ? '返回阅读' : mistakes ? '返回错题列表' : '返回书架'}</button>
      <span>{book?.title ?? modeLabel}</span><small>句子还原</small></header>
    <p className="practice-note">根据原文进行选择。</p>
    {session && !summary && missingBooks.length ? <section><h1 ref={heading} tabIndex={-1}>导入对应资源包后继续</h1>
      <p>本轮需要的书暂未导入：{missingBooks.join('、')}。</p><p>作答记录已保留。导入对应级别资源包后，可以继续这轮练习。</p>
      <button className="primary" onClick={onExit}>返回书架</button></section> : session && !summary && preparedSession !== session.id ? <p role="status">正在准备本轮练习…</p> : reviewQuestion ? <section className="practice-lesson-view">
      <h1 ref={heading} tabIndex={-1}>回到书里读一读</h1>
      {reviewPage ? <SentenceLesson page={reviewPage} unitId={reviewQuestion.source.unitId}/> : <><p role={lessonError ? 'alert' : 'status'}>{lessonError ? '讲解暂时没能打开，请重试。' : '正在打开书中讲解…'}</p>{lessonError && <button onClick={() => setLessonAttempt(n => n + 1)}>重新加载讲解</button>}</>}
      <button className="primary practice-primary" onClick={() => setReviewQuestion(null)}>返回练习<ArrowRight size={18}/></button>
    </section> : summary && session ? <section className="practice-summary">
      <h1 ref={heading} tabIndex={-1}>{session.completedAt !== null ? '本次还原完成' : '这轮的作答记录'}</h1>
      <p className="practice-lead">把表达放回句子，也记一记它在这里的意思。</p>
      <div className="practice-score"><strong>{initial.filter(isCorrect).length}<span> / {initial.length}</span></strong>
        <span>首次独立还原正确</span><small>{initial.filter(a => a.helpUsed).length} 题先看了讲解</small></div>
      {!!retries.length && <p className="practice-note">看过讲解后，再试还原正确 {retries.filter(isCorrect).length} / {retries.length} 题。还不熟悉的表达，之后再复习。</p>}
      <p className="practice-note">工具会安排隔天复习，这次完成后就可以继续读书了。</p>
      <div className="practice-summary-actions"><button className="primary practice-primary" onClick={onExit}>{book ? '继续阅读' : mistakes ? '返回错题列表' : '回到书架'}<ArrowRight size={18}/></button>
        <button className="practice-text-button" onClick={home}>练习首页</button>
        {onMistakes && <button className="practice-text-button" onClick={onMistakes}>错题练习<ArrowRight size={18}/></button>}</div>
      <div className="practice-results">{initial.filter(a => a.submittedAt !== null).map((a, i) => <details key={questionIdentity(a.bookId, a.question.id)}>
        <summary><span className={isCorrect(a) ? 'correct' : 'incorrect'}>{isCorrect(a) ? '✓' : '○'}</span>
          <span>第 {i + 1} 题 · {a.question.blank.text}</span><small>查看讲解</small></summary>
        <QuestionText question={a.question} revealed/><p className="practice-note">第一次：{selectedWord(a)}</p>{feedback(a.question)}
      </details>)}</div>
    </section> : showAnswer && session && attempt && question ? <section className="practice-answer-view" aria-label="答案与讲解">
      <div className="practice-progress-label"><span>放回原句，看看意思</span><span>{countLabel}</span></div>
      <h1 ref={heading} tabIndex={-1} className={isCorrect(attempt) ? 'correct' : ''}>{isCorrect(attempt) ? '还原正确' : '一起看看这个表达'}</h1>
      <QuestionText question={question} revealed/>
      {!isCorrect(attempt) && <p className="practice-note">这次：{selectedWord(attempt)}</p>}
      {feedback(question)}
      <div className="practice-answer-actions"><button className="primary practice-primary" onClick={() => update(s => advanceSession(s))}>
        {session.index === session.attempts.length - 1 ? '看看这轮收获' : session.attempts[session.index + 1].retry ? '再试一句' : '下一句'}<ArrowRight size={18}/>
      </button></div>
    </section> : session && attempt && question ? <section className="practice-question">
      <div className="practice-progress-label"><span>{attempt.retry ? '看过讲解，再试一次' : book ? '本书句子还原' : modeLabel}</span><span>{countLabel}</span></div>
      <h1 ref={heading} tabIndex={-1}>{attempt.retry ? '再试一次' : `第 ${session.index + 1} 句`}</h1>
      {!book && <p className="practice-note">来自 {attempt.bookTitle}</p>}
      <QuestionText question={question}/>
      <fieldset className="practice-options" disabled={attempt.submittedAt !== null}><legend>从这组词中，选出适合这句话的表达</legend>
        {attempt.optionOrder.map((id, i) => { const option = attempt.options.find(o => o.id === id)!;
          return <label key={id} data-selected={attempt.selectedId === id}>
            <input type="radio" name="practice-option" aria-label={option.text} checked={attempt.selectedId === id} value={id}
              onChange={() => update(s => selectAnswer(s, id))}/>
            <span className="practice-option-letter">{String.fromCharCode(65 + i)}</span><span lang="en">{option.text}</span>
            {attempt.selectedId === id && <Check size={18}/>}
          </label>;
        })}</fieldset>
      {attempt.submittedAt === null ? <div className="practice-question-actions">
        <button className="primary practice-primary" disabled={!attempt.selectedId} onClick={() => update(s => submitAnswer(s))}>确认答案<ArrowRight size={18}/></button>
        <button className="practice-text-button" onClick={() => update(s => submitAnswer(s, true))}>不会，看看讲解</button>
      </div> : <button className="primary practice-primary" onClick={() => setReturnedQuestion(null)}>查看答案与讲解<ArrowRight size={18}/></button>}
    </section> : <section className="practice-home">
      <span className="practice-eyebrow">{book ? '读完一本，再认一认' : '以前学过的，再认一认'}</span>
      <h1 ref={heading} tabIndex={-1}>{book ? '把表达放回书里的句子' : '今天复习两句'}</h1>
      {record.updateNotice && <p className="practice-note" role="status">练习内容已更新，请重新开始。</p>}
      <div className="practice-intro-card">{book?.practice ? <>
        <p><b>{book.practice.questions.length} 个句子 · {book.practice.options.length} 个表达</b></p>
        <p>从七个表达中选词还原五个句子，有两个表达用不上。它们都来自这本书。选过的词仍会保留，不会时可以直接看讲解。</p>
        <button className="primary practice-primary" onClick={() => start(newSession(book))}>开始句子还原<ArrowRight size={18}/></button>
      </> : reviewItems?.length ? <>
        <p>有 {reviewItems.length} 个表达到了复习时间，这次先练 {Math.min(2, reviewItems.length)} 句。</p>
        <p>每句话保留它原来那组词，不会时可以看讲解。</p>
        <button className="primary practice-primary" onClick={() => start(newReviewSession(reviewItems))}>开始复习<ArrowRight size={18}/></button>
      </> : <>
        <p>今天没有到期的复习。</p><p>完成读后练习后，工具会安排下次复习。</p>
        <button className="primary practice-primary" onClick={onExit}>去读书<ArrowRight size={18}/></button>
      </>}</div>{history}
    </section>}
    <footer className="practice-footer">一次学一点，下次再遇见。</footer>
  </div></div>;
}
