import { LegalNotices } from './LegalNotices';
import { version as appVersion } from '../package.json';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TouchEvent } from 'react';
import { App as NativeApp } from '@capacitor/app';
import { Capacitor } from '@capacitor/core';
import { ArrowLeft, ArrowRight, BookOpen, Check, List, RotateCcw, Search, Settings2, X } from 'lucide-react';
import type { Book, FontSize, ReaderState, TextSelection } from './types';
import { BookCover } from './BookCover';
import { OriginalPage } from './OriginalPage';
import { SentenceLesson } from './SentenceLesson';
import { readState, writeState } from './storage';
import { useContent } from './content';
import { BackupSettings } from './BackupSettings';
import { ResourceSettings } from './ResourceSettings';
import { commitImport, recoverImport } from './backup';
import { captureSelection } from './selection';
import { PracticeScreen } from './PracticeScreen';
import { MistakesScreen } from './MistakesScreen';
import { emptyPracticeRecord, readPracticeState, writePracticeState, reviewQueue, mistakeItems,
  type PracticeBookRecord, type PracticeState } from './practice';

const levelOf = (book: Book) => book.level?.trim() || '未分级';
const levelStorageKey = 'raz-reader-level';
type Dialog = 'pages' | 'settings' | 'resources' | 'sentence' | 'context' | 'restart' | null;

function Logo() {
  return <span className="brand"><span className="brand-icon"><BookOpen size={22}/></span><span><b>RAZ <span>小小精读</span></b><small>ONE PAGE AT A TIME</small></span></span>;
}

export default function App() {
  const { library, loadBook, retainBooks } = useContent();
  const availableLevels = library.levels.map(level => level.id);
  const [state, setState] = useState<ReaderState | null>(null);
  const [practiceState, setPracticeState] = useState<PracticeState | null>(null);
  const [practiceSaveError, setPracticeSaveError] = useState(false);
  const [readError, setReadError] = useState(false);
  const [readAttempt, setReadAttempt] = useState(0);
  const [importError, setImportError] = useState(false);
  const [importing, setImporting] = useState(false);
  const [resourceBusy, setResourceBusy] = useState(false);
  const [bookError, setBookError] = useState(false);
  const [bookAttempt, setBookAttempt] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [search, setSearch] = useState('');
  const [showSearch, setShowSearch] = useState(false);
  const [filter, setFilter] = useState<'all' | 'started'>('all');
  const [selectedLevel, setSelectedLevel] = useState(() => {
    try {
      const saved = window.localStorage.getItem(levelStorageKey);
      if (saved && availableLevels.includes(saved)) return saved;
    } catch {
      // Falling back to the first available level is safe when storage is unavailable.
    }
    return availableLevels.includes('P') ? 'P' : availableLevels[0] ?? '未分级';
  });
  const [restartTarget, setRestartTarget] = useState<Book | null>(null);
  const [saveError, setSaveError] = useState(false);
  const [selection, setSelection] = useState<TextSelection | null>(null);
  const [context, setContext] = useState<TextSelection | null>(null);
  const [focusedUnit, setFocusedUnit] = useState<string | null>(null);
  const [showSwipeHint, setShowSwipeHint] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const readerRef = useRef<HTMLDivElement>(null);
  const explanationRef = useRef<HTMLDivElement>(null);
  const selectedDuringTouch = useRef(false);
  const touch = useRef<{ x: number; y: number; time: number; blocked: boolean } | null>(null);
  const lessonTouch = useRef<{ x: number; y: number; time: number; blocked: boolean } | null>(null);
  const pinch = useRef<{ start: number; current: number } | null>(null);
  const cardPressTimer = useRef<number | null>(null);
  const suppressCardOpen = useRef(false);
  const book = library.books.find(item => item.id === state?.activeBookId);
  const reviewOpen = practiceState?.activeReview === true;
  const mistakesOpen = practiceState?.activeMistakes === true;
  const practiceOpen = reviewOpen || mistakesOpen || (!!book?.practice && practiceState?.activeBookId === book.id);
  const [reviewClock, setReviewClock] = useState(Date.now);
  useEffect(() => {
    const refresh = () => setReviewClock(Date.now());
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => { window.clearInterval(timer); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, []);
  const dueReviews = useMemo(() => practiceState ? reviewQueue(practiceState, library, reviewClock) : [], [practiceState, library, reviewClock]);
  const mistakes = useMemo(() => practiceState ? mistakeItems(practiceState, library) : [], [practiceState, library]);
  const pageId = book ? state?.progress[book.id]?.pageId ?? book.pageIds[0] : null;
  const page = pageId ? library.pages[pageId] : null;
  const pageIndex = book && page ? book.pageIds.indexOf(page.id) : 0;
  const explaining = dialog === 'sentence';
  const isTouchDevice = navigator.maxTouchPoints > 0;

  const clearSelection = useCallback(() => {
    window.getSelection()?.removeAllRanges(); setSelection(null);
  }, []);
  const closeDialog = useCallback(() => {
    if (importing || resourceBusy) return;
    clearSelection(); setContext(null);
    if (dialog === 'restart') { setRestartTarget(null); suppressCardOpen.current = false; }
    setDialog(dialog === 'context' ? 'sentence' : null);
  }, [dialog, clearSelection, importing, resourceBusy]);
  const openSentence = (unitId: string) => {
    clearSelection(); setContext(null); setFocusedUnit(unitId); setDialog('sentence');
  };

  useEffect(() => {
    let active = true;
    setReadError(false);
    // Do not expose or persist empty fallback state after a failed native read.
    void recoverImport().then(() => Promise.all([readState(library), readPracticeState(library)]))
      .then(([reader, practice]) => { if (active) { setState(reader); setPracticeState(practice); } })
      .catch(() => { if (active) setReadError(true); });
    return () => { active = false; };
  }, [readAttempt]);

  useEffect(() => {
    if (!book) return;
    const release = retainBooks([book.id]);
    let active = true;
    setBookError(false);
    void loadBook(book.id).catch(() => { if (active) setBookError(true); });
    return () => { active = false; release(); };
  }, [book?.id, bookAttempt, loadBook, retainBooks]);

  useEffect(() => {
    if (!state || importing || importError) return;
    let active = true;
    void writeState(state).then(() => { if (active) setSaveError(false); }).catch(() => { if (active) setSaveError(true); });
    return () => { active = false; };
  }, [state]);

  useEffect(() => {
    if (!practiceState || importing || importError) return;
    let active = true;
    void writePracticeState(practiceState).then(() => { if (active) setPracticeSaveError(false); }).catch(() => { if (active) setPracticeSaveError(true); });
    return () => { active = false; };
  }, [practiceState]);

  const importRecords = async (records: { reader: ReaderState; practice: PracticeState }) => {
    setImporting(true);
    try {
      await commitImport(records.reader, records.practice);
      setState(records.reader); setPracticeState(records.practice);
      setSaveError(false); setPracticeSaveError(false);
    } catch {
      setImportError(true);
      throw new Error('导入尚未完成，请重新打开以继续恢复。');
    } finally { setImporting(false); }
  };

  const refreshResources = async () => {
    if (state && practiceState) await Promise.all([writeState(state), writePracticeState(practiceState)]);
    window.location.reload();
  };

  const exitPractice = useCallback(() => {
    setPracticeState(current => current && ({ ...current, activeBookId: null, activeReview: false, activeMistakes: false }));
  }, []);
  const openPractice = () => {
    if (!book?.practice) return;
    clearSelection(); setDialog(null);
    setPracticeState(current => current && ({ ...current, activeBookId: book.id, activeReview: false, activeMistakes: false }));
  };
  const openReview = () => {
    setReviewClock(Date.now());
    clearSelection(); setDialog(null);
    setPracticeState(current => {
      if (!current) return current;
      const active = current.review.sessions.find(s => s.id === current.review.currentSessionId);
      return { ...current, activeBookId: null, activeReview: true, activeMistakes: false,
        review: active?.completedAt != null ? { ...current.review, currentSessionId: null } : current.review };
    });
  };
  const openMistakes = () => {
    clearSelection(); setDialog(null);
    setPracticeState(current => current && ({ ...current, activeBookId: null, activeReview: false, activeMistakes: true }));
  };
  const changeMistakes = (update: (record: PracticeBookRecord) => PracticeBookRecord) => {
    setPracticeState(current => current && ({ ...current, mistakes: update(current.mistakes) }));
  };
  const changeReview = (update: (record: PracticeBookRecord) => PracticeBookRecord) => {
    setPracticeState(current => current && ({ ...current, review: update(current.review) }));
  };
  const changePractice = (update: (record: PracticeBookRecord) => PracticeBookRecord) => {
    if (!book) return;
    setPracticeState(current => current && ({ ...current, books: {
      ...current.books, [book.id]: update(current.books[book.id] ?? emptyPracticeRecord()),
    } }));
  };

  useEffect(() => {
    try { window.localStorage.setItem(levelStorageKey, selectedLevel); } catch {
      // Level persistence is optional when browser storage is unavailable.
    }
  }, [selectedLevel]);

  const openBook = useCallback((target: Book, fromStart = false) => {
    setState(current => current && ({ ...current, activeBookId: target.id, progress: {
      ...current.progress, [target.id]: {
        pageId: fromStart ? target.pageIds[0] : current.progress[target.id]?.pageId ?? target.pageIds[0],
        updatedAt: Date.now(),
      },
    } }));
  }, []);

  const cancelCardPress = () => {
    if (cardPressTimer.current !== null) window.clearTimeout(cardPressTimer.current);
    cardPressTimer.current = null;
  };
  const showRestart = (target: Book) => {
    cancelCardPress(); suppressCardOpen.current = true;
    setRestartTarget(target); setDialog('restart');
  };
  const beginCardPress = (target: Book, hasProgress: boolean) => {
    suppressCardOpen.current = false;
    if (!hasProgress) return;
    cancelCardPress();
    cardPressTimer.current = window.setTimeout(() => showRestart(target), 600);
  };
  const openCard = (target: Book) => {
    cancelCardPress();
    if (suppressCardOpen.current) { suppressCardOpen.current = false; return; }
    openBook(target);
  };

  useEffect(() => () => {
    if (cardPressTimer.current !== null) window.clearTimeout(cardPressTimer.current);
  }, []);

  const goShelf = useCallback(() => {
    setDialog(null);
    setState(current => current && ({ ...current, activeBookId: null }));
  }, []);

  const goPage = useCallback((index: number) => {
    if (!book || index < 0 || index >= book.pageIds.length) return;
    setDialog(null);
    setState(current => current && ({ ...current, progress: {
      ...current.progress, [book.id]: { pageId: book.pageIds[index], updatedAt: Date.now() },
    } }));
  }, [book]);

  const turnPage = useCallback((offset: -1 | 1) => {
    if (!book) return;
    setDialog(null);
    setState(current => {
      if (!current) return current;
      const currentId = current.progress[book.id]?.pageId ?? book.pageIds[0];
      const nextIndex = book.pageIds.indexOf(currentId) + offset;
      if (nextIndex < 0 || nextIndex >= book.pageIds.length) return current;
      return { ...current, progress: {
        ...current.progress, [book.id]: { pageId: book.pageIds[nextIndex], updatedAt: Date.now() },
      } };
    });
  }, [book]);

  const turnSentence = useCallback((offset: -1 | 1) => {
    if (!page || !book || !focusedUnit) return;
    const hotspotIds = new Set(page.paper.hotspots.map(spot => spot.unitId));
    const sentenceIds = new Set(page.blocks.filter(block => block.kind === 'sentence').flatMap(block => block.sourceRefs));
    // Lesson blocks follow reading order; source units may have been appended during revisions.
    const unitIds = [...new Set([
      ...page.blocks.filter(block => ['sentence', 'vocabulary', 'structure'].includes(block.kind))
        .flatMap(block => block.sourceRefs.filter(id => block.kind === 'sentence' || !sentenceIds.has(id))),
      ...page.sourceUnits.map(unit => unit.id),
    ])].filter(id => hotspotIds.has(id));
    const next = unitIds.indexOf(focusedUnit) + offset;
    clearSelection(); setContext(null);
    if (next >= 0 && next < unitIds.length) {
      setFocusedUnit(unitIds[next]);
      return;
    }
    if (pageIndex + offset >= 0 && pageIndex + offset < book.pageIds.length) turnPage(offset);
  }, [page, book, focusedUnit, pageIndex, clearSelection, turnPage]);

  useEffect(() => {
    setSelection(null); setContext(null); setFocusedUnit(null);
    window.getSelection()?.removeAllRanges();
    readerRef.current?.scrollTo({ top: 0, behavior: 'instant' });
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [pageId]);

  useEffect(() => {
    if (dialog === 'sentence') explanationRef.current?.scrollTo({ top: 0, behavior: 'instant' });
  }, [dialog, focusedUnit]);

  useEffect(() => {
    if (!page) return;
    const handler = () => {
      if (!explaining) return;
      const root = explanationRef.current;
      const captured = root ? captureSelection(root, page, library) : null;
      if (captured) selectedDuringTouch.current = true;
      setSelection(captured);
    };
    document.addEventListener('selectionchange', handler);
    return () => document.removeEventListener('selectionchange', handler);
  }, [page, explaining]);

  useEffect(() => {
    const element = dialogRef.current;
    if (dialog && !element?.open) element?.showModal();
    else if (!dialog && element?.open) element.close();
  }, [dialog]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (practiceOpen || !book || !window.getSelection()?.isCollapsed ||
        (event.target as Element).closest('input, textarea, select')) return;
      if (dialog === 'sentence') {
        if (event.key === 'ArrowRight') { event.preventDefault(); turnSentence(1); }
        if (event.key === 'ArrowLeft') { event.preventDefault(); turnSentence(-1); }
        return;
      }
      if (dialog) return;
      if (event.key === 'Escape') { event.preventDefault(); goShelf(); }
      if (event.key === 'ArrowRight') { event.preventDefault(); turnPage(1); }
      if (event.key === 'ArrowLeft') { event.preventDefault(); turnPage(-1); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [book, dialog, goShelf, turnPage, turnSentence, practiceOpen]);

  useEffect(() => {
    if (!book || !isTouchDevice) return;
    const key = 'raz-reader-swipe-hint-seen';
    try {
      if (window.localStorage.getItem(key)) return;
      window.localStorage.setItem(key, '1');
    } catch {
      // The hint is optional when browser storage is unavailable.
    }
    setShowSwipeHint(true);
    const timer = window.setTimeout(() => setShowSwipeHint(false), 2800);
    return () => window.clearTimeout(timer);
  }, [book, isTouchDevice]);

  useEffect(() => {
    if (practiceOpen || !Capacitor.isNativePlatform()) return;
    const handle = NativeApp.addListener('backButton', () => {
      if (dialog) closeDialog();
      else if (selection) { window.getSelection()?.removeAllRanges(); setSelection(null); }
      else if (book) goShelf();
      else void NativeApp.exitApp();
    });
    return () => { void handle.then(listener => listener.remove()); };
  }, [book, dialog, selection, goShelf, closeDialog, practiceOpen]);

  const touchStart = (event: TouchEvent) => {
    if (event.touches.length === 2) {
      const distance = Math.hypot(event.touches[0].clientX - event.touches[1].clientX,
        event.touches[0].clientY - event.touches[1].clientY);
      pinch.current = { start: distance, current: distance };
      touch.current = null;
      return;
    }
    if (event.touches.length !== 1) { touch.current = null; pinch.current = null; return; }
    pinch.current = null;
    const point = event.touches[0];
    selectedDuringTouch.current = !window.getSelection()?.isCollapsed;
    touch.current = { x: point.clientX, y: point.clientY, time: performance.now(),
      blocked: !!(event.target as Element).closest('button, summary, a, input, [data-zoomed="true"], .selection-bar') };
  };
  const touchMove = (event: TouchEvent) => {
    if (!pinch.current || event.touches.length !== 2) return;
    pinch.current.current = Math.hypot(event.touches[0].clientX - event.touches[1].clientX,
      event.touches[0].clientY - event.touches[1].clientY);
  };
  const touchEnd = (event: TouchEvent) => {
    if (pinch.current) {
      const gesture = pinch.current; pinch.current = null; touch.current = null;
      if (gesture.start - gesture.current > Math.max(60, gesture.start * .25)) goShelf();
      return;
    }
    const start = touch.current; touch.current = null;
    if (!start || start.blocked || dialog || selectedDuringTouch.current || !window.getSelection()?.isCollapsed) return;
    const point = event.changedTouches[0];
    if (!point) return;
    const dx = point.clientX - start.x, dy = point.clientY - start.y;
    if (performance.now() - start.time < 450 && Math.abs(dx) > 90 && Math.abs(dx) > Math.abs(dy) * 1.8) {
      turnPage(dx < 0 ? 1 : -1);
    }
  };

  const lessonTouchStart = (event: TouchEvent) => {
    if (event.touches.length !== 1) { lessonTouch.current = null; return; }
    const point = event.touches[0];
    selectedDuringTouch.current = !window.getSelection()?.isCollapsed;
    lessonTouch.current = { x: point.clientX, y: point.clientY, time: performance.now(),
      blocked: !!(event.target as Element).closest('button, summary, a, input, .selection-bar') };
  };
  const lessonTouchEnd = (event: TouchEvent) => {
    const start = lessonTouch.current; lessonTouch.current = null;
    if (!start || start.blocked || selectedDuringTouch.current || !window.getSelection()?.isCollapsed) return;
    const point = event.changedTouches[0];
    if (!point) return;
    const dx = point.clientX - start.x, dy = point.clientY - start.y;
    if (performance.now() - start.time < 450 && Math.abs(dx) > 90 && Math.abs(dx) > Math.abs(dy) * 1.8) {
      turnSentence(dx < 0 ? 1 : -1);
    }
  };

  const levelBooks = library.books.filter(item => levelOf(item) === selectedLevel);
  const recentBook = state ? levelBooks.filter(b => state.progress[b.id])
    .sort((a, b) => state.progress[b.id].updatedAt - state.progress[a.id].updatedAt)[0] : undefined;

  useEffect(() => {
    if (!state || state.activeBookId || practiceOpen || !recentBook) return;
    const frame = window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>('.book-card[data-last-read="true"]')
        ?.scrollIntoView({ block: 'center', behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [state?.activeBookId, recentBook?.id, selectedLevel, practiceOpen]);

  if (importError) return <div className="loading-screen" role="alert"><Logo/><h1>记录导入尚未完成</h1><p>重新打开后会继续恢复这次备份。</p><button className="primary" onClick={() => location.reload()}>重新打开</button></div>;
  if (!state || !practiceState) return <div className="loading-screen" role={readError ? 'alert' : 'status'}><Logo/>{readError ? <><h1>学习记录暂时没能读取</h1><p>原有记录已保留，请重试。</p><button className="primary" onClick={() => setReadAttempt(n => n + 1)}>重新读取记录</button></> : <p>正在打开你的书架…</p>}</div>;
  const startedCount = levelBooks.filter(item => state.progress[item.id]).length;
  const visibleBooks = levelBooks.filter(b =>
    `${b.title} ${b.subtitle}`.toLowerCase().includes(search.toLowerCase()) &&
    (filter === 'all' || state.progress[b.id]));

  return <div className={`app ${book || practiceOpen ? 'is-reading' : 'is-shelf'}`} data-font={state.fontSize}>
    <main className="main-area">
      {mistakesOpen ? <MistakesScreen library={library} items={mistakes} record={practiceState.mistakes}
        exitLabel={book ? '返回阅读' : '返回书架'} onChange={changeMistakes} onExit={exitPractice}/> : reviewOpen ? <PracticeScreen key="review" library={library} record={practiceState.review} reviewItems={dueReviews}
        onChange={changeReview} onExit={exitPractice}/> : practiceOpen && book ? <PracticeScreen key={book.id} book={book} library={library}
        record={practiceState.books[book.id] ?? emptyPracticeRecord()} onChange={changePractice} onExit={exitPractice}
        onMistakes={openMistakes}/> : !book ? <>
        <div className="shelf-content">
          <div className="library-heading"><div className="library-title"><h2>我的书籍</h2><div className="level-switch" role="group" aria-label="选择级别">
            {availableLevels.map(level => <button type="button" key={level} aria-label={`${level} 级`} aria-pressed={selectedLevel === level} className={selectedLevel === level ? 'selected' : ''} onClick={() => { setSelectedLevel(level); setFilter('all'); setSearch(''); }}>{level}<span>级</span></button>)}
          </div></div><div className="library-controls"><div className="filters" aria-label="书籍筛选"><button className={filter === 'all' ? 'selected' : ''} aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部 <span>{levelBooks.length}</span></button><button className={filter === 'started' ? 'selected' : ''} aria-pressed={filter === 'started'} onClick={() => setFilter('started')}>读过的 <span>{startedCount}</span></button></div><div className="shelf-actions"><button className="icon-button review-entry" aria-label="错题练习" onClick={() => openMistakes()}><BookOpen size={20}/>错题练习</button><button className="icon-button review-entry" aria-label="隔天复习" onClick={openReview}><RotateCcw size={20}/>隔天复习{dueReviews.length > 0 && <span className="review-count">{dueReviews.length}</span>}</button><button className="icon-button" aria-label={showSearch ? '收起搜索' : '搜索书籍'} aria-expanded={showSearch} onClick={() => setShowSearch(value => !value)}><Search size={20}/></button><button className="icon-button" aria-label="阅读设置" onClick={() => setDialog('settings')}><Settings2 size={20}/></button></div></div></div>
          {showSearch && <label className="search shelf-search"><Search size={18}/><input autoFocus aria-label="搜索书籍" placeholder="输入英文或中文书名" value={search} onChange={e => setSearch(e.target.value)}/></label>}
          <div className="resource-shelf-entry"><button onClick={() => setDialog('resources')}>管理资源包</button><span>按需导入各级别，离线阅读</span></div>
          <div className="book-grid">{visibleBooks.map(item => {
            const progress = state.progress[item.id];
            const current = progress ? item.pageIds.indexOf(progress.pageId) + 1 : 0;
            return <button className="book-card" key={item.id} data-level={levelOf(item)} data-last-read={recentBook?.id === item.id || undefined} aria-label={`打开 ${item.title}`} aria-description={progress ? '长按可从头读' : undefined}
              onPointerDown={() => beginCardPress(item, !!progress)} onPointerUp={cancelCardPress} onPointerCancel={cancelCardPress} onPointerLeave={cancelCardPress}
              onContextMenu={event => { if (progress) { event.preventDefault(); showRestart(item); } }} onClick={() => openCard(item)}>
              <BookCover book={item}/><span className="book-info"><b>{item.title}</b><span>{item.subtitle}</span>{progress ? <span className="book-progress"><span className="progress-track"><i style={{ width: `${current / item.pageIds.length * 100}%` }}/></span><small>第 {current} / {item.pageIds.length} 页</small></span> : <small className="unread">未开始</small>}</span>
            </button>;
          })}</div>
          {!library.books.length && <div className="empty-state"><BookOpen size={36}/><h3>导入第一个级别，开始阅读</h3><p>从电脑或 Pad 选择 .razpack 资源包，导入后即可离线使用。</p><button className="primary" onClick={() => setDialog('resources')}>导入资源包</button></div>}
          {!!library.books.length && !visibleBooks.length && <div className="empty-state"><BookOpen size={36}/><h3>{search ? '没有找到这本书' : '第一本书，正等着你'}</h3><p>{search ? '试试英文书名或中文书名。' : '选择“全部书籍”，开始今天的阅读吧。'}</p><button onClick={() => { setSearch(''); setFilter('all'); }}>查看全部书籍</button></div>}
          <footer className="shelf-footer"><BookOpen size={16}/>RAZ 小小精读 <span>·</span> 每一页，都是一点进步。</footer>
        </div>
      </> : page && <>
        <div className="reading-scroll" ref={readerRef} onTouchStart={touchStart} onTouchMove={touchMove} onTouchEnd={touchEnd} onTouchCancel={() => { touch.current = null; pinch.current = null; }}>
          <OriginalPage key={page.id} page={page} focusedUnit={focusedUnit} onSentence={openSentence}/>
        </div>
        <button className="reader-back" onClick={goShelf}><ArrowLeft size={17}/>返回书架</button>
        <button key={page.id} className="page-indicator" aria-label={`选择页码，当前第 ${pageIndex + 1} 页，共 ${book.pageIds.length} 页`} onClick={() => setDialog('pages')}><List size={15}/><b>{pageIndex + 1}</b><span>/ {book.pageIds.length}</span></button>
        {book.practice && pageIndex === book.pageIds.length - 1 && <button className="practice-end-entry" onClick={openPractice}>读后练习 →</button>}
        {showSwipeHint && <div className="swipe-hint" role="status">左右滑动翻页</div>}
      </>}
      {book && !practiceOpen && !page && <div className="loading-screen" role={bookError ? 'alert' : 'status'}><h1>{book.title}</h1><p>{bookError ? '这本书暂时没能打开，请重试。' : '正在翻开这本书…'}</p>{bookError && <button className="primary" onClick={() => setBookAttempt(n => n + 1)}>重新加载本书</button>}<button onClick={goShelf}>返回书架</button></div>}
      {saveError && <div className="save-error" role="alert">这次进度暂未保存。你可以继续阅读，重新打开前请记下页码。</div>}
    </main>
    {practiceSaveError && <div className="practice-save-error" role="alert">练习记录暂未保存。可以继续练习，关闭前请留意本次结果。</div>}

    <dialog ref={dialogRef} className={`modal modal-${dialog}`} aria-label={dialog === 'sentence' ? '句子讲解' : undefined} aria-labelledby={dialog === 'sentence' ? undefined : 'dialog-title'} onCancel={event => { event.preventDefault(); closeDialog(); }} onClose={() => setDialog(null)} onClick={event => { if (event.target === event.currentTarget) closeDialog(); }}>
      {importing && <div className="import-overlay" role="status">正在合并学习记录，请稍候…</div>}
      <div className="modal-inner" inert={importing || undefined}>{dialog !== 'sentence' && <header><h2 id="dialog-title">{dialog === 'pages' ? '想读哪一页？' : dialog === 'context' ? '回到原句，读一读' : dialog === 'restart' ? '重新开始这本书？' : dialog === 'resources' ? '资源包管理' : '让阅读更舒服'}</h2><button className="icon-button" aria-label={dialog === 'context' ? '返回讲解' : '关闭'} onClick={closeDialog}><X size={21}/></button></header>}
        {dialog === 'resources' && <ResourceSettings onChanged={refreshResources} onBusy={setResourceBusy}/>}
        {explaining && <div className="sentence-toolbar"><span>句子讲解</span><button className="icon-button" aria-label="关闭讲解" onClick={closeDialog}><X size={21}/></button></div>}
        {dialog === 'pages' && book && <><p className="modal-description">{book.title}</p>{book.practice && <button className="practice-entry" onClick={openPractice}><BookOpen size={18}/>{practiceState.books[book.id]?.currentSessionId ? '继续读后练习' : '读后练习'}<ArrowRight size={16}/></button>}<div className="page-grid">{book.pageIds.map((id, index) => <button key={id} aria-label={`跳转到第 ${index + 1} 页`} aria-current={id === pageId ? 'page' : undefined} className={id === pageId ? 'current' : ''} onClick={() => goPage(index)}><b>{index + 1}</b><span>{library.pages[id].label}</span></button>)}</div><p className="modal-footnote">按讲义顺序排列，纸书页码显示在下方。</p></>}
        {dialog === 'settings' && <><p className="modal-description">选择讲解文字的大小。</p><div className="font-options" role="group" aria-label="文字大小">{([['standard', '标准'], ['large', '大字'], ['larger', '更大']] as [FontSize, string][]).map(([size, label], index) => <button key={size} aria-pressed={state.fontSize === size} className={state.fontSize === size ? 'selected' : ''} onClick={() => setState(current => current && ({ ...current, fontSize: size }))}><span style={{ fontSize: `${18 + index * 4}px` }}>Aa</span>{label}{state.fontSize === size && <Check size={15}/>}</button>)}</div><div className="font-preview"><span>Read a little. Discover a lot.</span><p>一点一点读，慢慢就懂了。</p></div><div className="reading-tips"><h3>阅读小贴士</h3><p>点原书上的句子，就能看讲解。</p><p>长按讲解中的文字可以选词，查看它关联的原句。</p><p>手机或平板左右滑动翻页，电脑使用键盘左右方向键。阅读位置会自动保存。</p></div><button className="resource-settings-entry" onClick={() => setDialog('resources')}>管理资源包</button><BackupSettings reader={state} practice={practiceState} library={library} onImport={importRecords}/><details className="about"><summary>关于这个版本</summary><p>RAZ 小小精读 · {appVersion}</p><p>当前已提供 {library.books.length} 本书、{library.pageCount ?? Object.keys(library.pages).length} 页原书与讲义，可离线阅读。内容来自自行导入的资源包，讲义含 AI 辅助编写内容，请家长结合原书核对。阅读器不含联网 AI、广告或统计服务。</p><p>阅读器代码采用 PolyForm Noncommercial 1.0.0 非商业许可，允许范围以协议为准。与 Learning A-Z 无隶属关系，书籍与音频的权利归原权利人所有。</p><LegalNotices/></details></>}
        {dialog === 'restart' && restartTarget && <><p className="modal-description">{restartTarget.title}</p><p className="restart-warning">当前阅读位置会重置到第一页。</p><div className="confirm-actions"><button onClick={closeDialog}>取消</button><button className="primary" onClick={() => { const target = restartTarget; setRestartTarget(null); setDialog(null); openBook(target, true); }}>从头读</button></div></>}
        {(explaining || dialog === 'context') && page && <div className="explanation-scroll" ref={explanationRef} hidden={dialog === 'context'} onTouchStart={lessonTouchStart} onTouchEnd={lessonTouchEnd} onTouchCancel={() => { lessonTouch.current = null; }}>
          {focusedUnit && <SentenceLesson key={`${page.id}:${focusedUnit}`} page={page} unitId={focusedUnit}/>}
        </div>}
        {selection && explaining && <div className="selection-bar" role="status"><span>已选：<b>{selection.selectedText.length > 24 ? selection.selectedText.slice(0, 24) + '…' : selection.selectedText}</b></span><button onPointerDown={e => e.preventDefault()} onClick={() => { setContext(selection); setDialog('context'); }}>查看所在原句<ArrowRight size={15}/></button><button className="clear-selection" aria-label="取消选择" onClick={clearSelection}><X size={17}/></button></div>}
        {dialog === 'context' && context && <><p className="selection-quote">“{context.selectedText}”</p><p className="modal-description">{context.sourceUnits.length ? '这段文字关联的原句' : '这段讲解没有关联英文原句。'}</p><div className="context-sentences">{context.sourceUnits.map(unit => <p key={unit.id} data-source-unit-id={unit.id} lang="en">{unit.text}</p>)}</div><button className="primary context-done" onClick={closeDialog}>继续看讲解<ArrowRight size={17}/></button></>}
      </div>
    </dialog>
  </div>;
}
