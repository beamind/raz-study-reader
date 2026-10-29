import { Preferences } from '@capacitor/preferences';
import type { Book, Library } from './types';

export interface PracticeOption { id: string; text: string }
export interface PracticeQuestion {
  id: string; revision: number; answerId: string;
  source: { text: string; pageId: string; unitId: string; blockId: string; lessonSha256: string; pageLabel: string };
  blank: { start: number; end: number; text: string };
  meaning: string; translation: string; explanation: string;
}
export interface PracticeSet {
  schemaVersion: 'raz-restoration/1.0'; bookId: string; version: string;
  options: PracticeOption[]; questions: PracticeQuestion[];
}
export interface PracticeAttempt {
  bookId: string; bookTitle: string; packVersion: string; question: PracticeQuestion;
  options: PracticeOption[]; optionOrder: string[]; selectedId: string | null;
  submittedAt: number | null; helpUsed: boolean; retry: boolean;
}
export interface PracticeSession {
  id: string; mode: 'book' | 'review' | 'mistakes'; startedAt: number; completedAt: number | null;
  index: number; attempts: PracticeAttempt[];
}
export interface PracticeBookRecord {
  currentSessionId: string | null; sessions: PracticeSession[]; updateNotice: boolean;
}
export interface PracticeState {
  schemaVersion: 'raz-restoration-state/1.0'; activeBookId: string | null; activeReview: boolean;
  books: Record<string, PracticeBookRecord>; review: PracticeBookRecord;
  activeMistakes: boolean; mistakes: PracticeBookRecord;
}
export interface ReviewItem {
  book: Book; pack: PracticeSet; question: PracticeQuestion; dueAt: number;
}
export interface MistakeItem {
  key: string; bookId: string; bookTitle: string; packVersion: string; question: PracticeQuestion;
  independentCount: number; wrongCount: number; helpCount: number; retryCount: number;
  errorRate: number | null; lastAttemptAt: number; lastMissedAt: number;
  needsPractice: boolean; history: PracticeAttempt[]; available: boolean;
}
export const PRACTICE_STORAGE_KEY = 'raz-restoration-v1';
export const DAY_MS = 86_400_000;
export const emptyPracticeRecord = (): PracticeBookRecord => ({ currentSessionId: null, sessions: [], updateNotice: false });
export const emptyPracticeState = (): PracticeState => ({
  schemaVersion: 'raz-restoration-state/1.0', activeBookId: null, activeReview: false,
  books: Object.create(null), review: emptyPracticeRecord(), activeMistakes: false, mistakes: emptyPracticeRecord(),
});
export const questionIdentity = (bookId: string, questionId: string) => JSON.stringify([bookId, questionId]);
export const isCorrect = (a: PracticeAttempt) => a.submittedAt !== null && !a.helpUsed && a.selectedId === a.question.answerId;

function shuffle<T>(items: readonly T[]): T[] {
  const result = [...items];
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}
function attemptFor(book: Book, pack: PracticeSet, question: PracticeQuestion, order: string[]): PracticeAttempt {
  return { bookId: book.id, bookTitle: book.title, packVersion: pack.version,
    question: structuredClone(question), options: structuredClone(pack.options), optionOrder: [...order],
    selectedId: null, submittedAt: null, helpUsed: false, retry: false };
}
function sessionFor(mode: PracticeSession['mode'], attempts: PracticeAttempt[], now: number): PracticeSession {
  if (!attempts.length) throw new RangeError('Cannot start an empty practice');
  return { id: `${now}-${Math.random().toString(36).slice(2)}`, mode, startedAt: now,
    completedAt: null, index: 0, attempts };
}
export function newSession(book: Book, now = Date.now()): PracticeSession {
  const pack = book.practice;
  if (!pack) throw new Error('Book has no practice');
  const order = shuffle(pack.options.map(o => o.id));
  return sessionFor('book', pack.questions.map(q => attemptFor(book, pack, q, order)), now);
}
export function newReviewSession(items: ReviewItem[], now = Date.now()): PracticeSession {
  const orders = new Map<string, string[]>();
  const seen = new Set<string>();
  const picked = items.filter(item => {
    const key = questionIdentity(item.book.id, item.question.id);
    if (item.dueAt > now || seen.has(key)) return false;
    seen.add(key); return true;
  }).slice(0, 2);
  return sessionFor('review', picked.map(({ book, pack, question }) => {
    if (!orders.has(book.id)) orders.set(book.id, shuffle(pack.options.map(o => o.id)));
    return attemptFor(book, pack, question, orders.get(book.id)!);
  }), now);
}
function records(state: PracticeState): PracticeBookRecord[] {
  return [...Object.values(state.books), state.review, state.mistakes];
}
// Keep counts derived from submitted attempts, including unfinished sessions. A content
// revision has its own statistics; old snapshots remain readable but cannot be practised.
export function mistakeItems(state: PracticeState, library: Library): MistakeItem[] {
  const items = new Map<string, MistakeItem>();
  const attempts = records(state).flatMap(r => r.sessions).flatMap(s => s.attempts)
    .filter(a => a.submittedAt !== null).sort((a, b) => a.submittedAt! - b.submittedAt!);
  for (const a of attempts) {
    const key = JSON.stringify([a.bookId, a.question.id, a.packVersion]);
    let item = items.get(key);
    if (!item) {
      const pack = library.books.find(b => b.id === a.bookId)?.practice;
      item = { key, bookId: a.bookId, bookTitle: a.bookTitle, packVersion: a.packVersion,
        question: a.question, independentCount: 0, wrongCount: 0, helpCount: 0, retryCount: 0,
        errorRate: null, lastAttemptAt: 0, lastMissedAt: 0, needsPractice: false, history: [],
        available: pack?.version === a.packVersion && pack.questions.some(q => q.id === a.question.id) };
      items.set(key, item);
    }
    item.history.push(a);
    if (a.retry) { item.retryCount++; continue; }
    item.lastAttemptAt = a.submittedAt!;
    item.needsPractice = !isCorrect(a);
    if (a.helpUsed) item.helpCount++;
    else { item.independentCount++; if (!isCorrect(a)) item.wrongCount++; }
    if (!isCorrect(a)) item.lastMissedAt = a.submittedAt!;
    item.errorRate = item.independentCount ? item.wrongCount / item.independentCount : null;
  }
  return [...items.values()].filter(i => i.wrongCount || i.helpCount).sort((a, b) =>
    Number(b.needsPractice) - Number(a.needsPractice) || a.lastAttemptAt - b.lastAttemptAt ||
    (b.wrongCount + b.helpCount) - (a.wrongCount + a.helpCount) || b.lastMissedAt - a.lastMissedAt || a.key.localeCompare(b.key));
}
export function newMistakeSession(items: MistakeItem[], library: Library, now = Date.now(), random = Math.random): PracticeSession {
  const seen = new Set<string>(), orders = new Map<string, string[]>();
  const pool: { book: Book; pack: PracticeSet; question: PracticeQuestion; weight: number }[] = [];
  for (const item of items) {
    const book = library.books.find(b => b.id === item.bookId), pack = book?.practice;
    const question = pack?.questions.find(q => q.id === item.question.id);
    const key = questionIdentity(item.bookId, item.question.id);
    if (!book || !pack || !question || pack.version !== item.packVersion || seen.has(key)) continue;
    seen.add(key);
    // A baseline keeps help-only items eligible. Counts exclude same-session retries.
    pool.push({ book, pack, question, weight: 1 + item.wrongCount });
  }
  const attempts: PracticeAttempt[] = [];
  while (pool.length && attempts.length < 5) {
    let draw = random() * pool.reduce((sum, item) => sum + item.weight, 0);
    let index = pool.length - 1;
    for (let i = 0; i < pool.length; i++) {
      if (draw < pool[i].weight) { index = i; break; }
      draw -= pool[i].weight;
    }
    // Remove every picked question before the next draw; save the result in the session.
    const { book, pack, question } = pool.splice(index, 1)[0];
    if (!orders.has(book.id)) orders.set(book.id, shuffle(pack.options.map(o => o.id)));
    attempts.push(attemptFor(book, pack, question, orders.get(book.id)!));
  }
  return sessionFor('mistakes', attempts, now);
}
export function selectAnswer(session: PracticeSession, id: string): PracticeSession {
  const a = session.attempts[session.index];
  if (session.completedAt !== null || a.submittedAt !== null || !a.options.some(o => o.id === id)) return session;
  return { ...session, attempts: session.attempts.map((item, i) => i === session.index ? { ...item, selectedId: id } : item) };
}
export function submitAnswer(session: PracticeSession, help = false, now = Date.now()): PracticeSession {
  const a = session.attempts[session.index];
  if (session.completedAt !== null || a.submittedAt !== null || (!help && !a.selectedId)) return session;
  const submitted = { ...a, helpUsed: help, selectedId: help ? null : a.selectedId, submittedAt: now };
  const attempts = session.attempts.map((item, i) => i === session.index ? submitted : item);
  // At most one retry per question. Keep the full bank, even when only one question was missed.
  if (!a.retry && !isCorrect(submitted)) {
    attempts.push({ ...structuredClone(a), selectedId: null, submittedAt: null, helpUsed: false, retry: true });
  }
  return { ...session, attempts };
}
export function advanceSession(session: PracticeSession, now = Date.now()): PracticeSession {
  if (session.completedAt !== null || session.attempts[session.index].submittedAt === null) return session;
  return session.index + 1 < session.attempts.length ? { ...session, index: session.index + 1 }
    : { ...session, completedAt: now };
}

// Review all learned expressions, including first-time successes. Same-session retries do not
// advance the schedule. Early repeats cannot turn short-term recognition into a longer interval.
export function reviewQueue(state: PracticeState, library: Library, now = Date.now()): ReviewItem[] {
  const events = records(state).flatMap(r => r.sessions)
    .flatMap(s => s.attempts).filter(a => !a.retry && a.submittedAt !== null)
    .sort((a, b) => a.submittedAt! - b.submittedAt!);
  const schedules = new Map<string, { stage: number; dueAt: number; item: ReviewItem }>();
  for (const a of events) {
    const book = library.books.find(b => b.id === a.bookId);
    const pack = book?.practice;
    const question = pack?.questions.find(q => q.id === a.question.id);
    if (!book || !pack || !question || pack.version !== a.packVersion) continue;
    const key = questionIdentity(book.id, question.id), old = schedules.get(key), time = a.submittedAt!;
    if (old && isCorrect(a) && time < old.dueAt) continue;
    const stage = isCorrect(a) && old ? Math.min(old.stage + 1, 2) : 0;
    const dueAt = time + [1, 3, 7][stage] * DAY_MS;
    schedules.set(key, { stage, dueAt, item: { book, pack, question, dueAt } });
  }
  return [...schedules.values()].map(s => s.item).filter(item => item.dueAt <= now)
    .sort((a, b) => a.dueAt - b.dueAt || questionIdentity(a.book.id, a.question.id).localeCompare(questionIdentity(b.book.id, b.question.id)));
}

const object = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
const strings = (v: Record<string, any>, keys: string[]) => keys.every(k => typeof v[k] === 'string' && !!v[k].trim());
const timeValue = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
function validAttempt(a: unknown): a is PracticeAttempt {
  if (!object(a) || !strings(a, ['bookId', 'bookTitle', 'packVersion']) ||
      typeof a.retry !== 'boolean' || typeof a.helpUsed !== 'boolean' ||
      !(a.submittedAt === null || timeValue(a.submittedAt))) return false;
  const q = a.question;
  if (!object(q) || !strings(q, ['id', 'answerId', 'meaning', 'translation', 'explanation']) ||
      !Number.isInteger(q.revision) || q.revision < 1 || !object(q.source) ||
      !strings(q.source, ['text', 'pageId', 'unitId', 'blockId', 'lessonSha256', 'pageLabel']) || !object(q.blank) ||
      !strings(q.blank, ['text']) || !Number.isInteger(q.blank.start) || !Number.isInteger(q.blank.end) ||
      q.blank.start < 0 || q.blank.end <= q.blank.start || q.blank.end > Array.from(q.source.text).length ||
      Array.from(q.source.text).slice(q.blank.start, q.blank.end).join('') !== q.blank.text) return false;
  if (!Array.isArray(a.options) || a.options.length !== 7 ||
      !a.options.every((o: unknown) => object(o) && strings(o, ['id', 'text'])) ||
      new Set(a.options.map((o: PracticeOption) => o.id)).size !== a.options.length ||
      new Set(a.options.map((o: PracticeOption) => o.text.trim().toLowerCase())).size !== a.options.length ||
      !a.options.some((o: PracticeOption) => o.id === q.answerId && o.text === q.blank.text) ||
      !Array.isArray(a.optionOrder) || a.optionOrder.length !== a.options.length ||
      new Set(a.optionOrder).size !== a.options.length ||
      !a.optionOrder.every((id: unknown) => a.options.some((o: PracticeOption) => o.id === id)) ||
      !(a.selectedId === null || a.options.some((o: PracticeOption) => o.id === a.selectedId))) return false;
  return a.submittedAt === null ? !a.helpUsed : (a.helpUsed ? a.selectedId === null : a.selectedId !== null);
}
export function validBookPractice(book: Book): boolean {
  const pack = book.practice;
  if (pack === undefined) return true;
  if (!object(pack) || pack.schemaVersion !== 'raz-restoration/1.0' || pack.bookId !== book.id || typeof pack.version !== 'string' || !pack.version ||
      !Array.isArray(pack.questions) || pack.questions.length !== 5 || !Array.isArray(pack.options) ||
      new Set(pack.questions.map(q => q?.id)).size !== pack.questions.length) return false;
  return pack.questions.every(question => validAttempt({ bookId: book.id, bookTitle: book.title, packVersion: pack.version,
    question, options: pack.options, optionOrder: pack.options.map(option => option?.id),
    selectedId: null, submittedAt: null, helpUsed: false, retry: false }) && book.pageIds.includes(question.source.pageId));
}
function validSession(s: unknown): s is PracticeSession {
  if (!object(s) || !strings(s, ['id']) || !['book', 'review', 'mistakes'].includes(s.mode) ||
      !timeValue(s.startedAt) || !(s.completedAt === null || timeValue(s.completedAt)) ||
      !Array.isArray(s.attempts) || !s.attempts.length || !s.attempts.every(validAttempt) ||
      !Number.isInteger(s.index) || s.index < 0 || s.index >= s.attempts.length) return false;
  const first = new Map<string, PracticeAttempt>(), retries = new Set<string>();
  for (const a of s.attempts as PracticeAttempt[]) {
    const key = questionIdentity(a.bookId, a.question.id);
    if (!a.retry) {
      if (first.has(key) || retries.size) return false;
      first.set(key, a);
    } else {
      const original = first.get(key);
      if (!original || original.submittedAt === null || isCorrect(original) || retries.has(key) ||
          JSON.stringify(original.question) !== JSON.stringify(a.question) ||
          JSON.stringify(original.options) !== JSON.stringify(a.options) ||
          JSON.stringify(original.optionOrder) !== JSON.stringify(a.optionOrder) ||
          original.packVersion !== a.packVersion) return false;
      retries.add(key);
    }
  }
  if (first.size > (s.mode === 'review' ? 2 : 5)) return false;
  return s.attempts.every((a: PracticeAttempt, i: number) =>
    (i >= s.index && s.completedAt === null) || a.submittedAt !== null);
}
// Imports must be complete; normal on-device recovery may salvage valid sessions.
export function validPracticeBackup(value: unknown): value is PracticeState {
  if (!object(value) || value.schemaVersion !== 'raz-restoration-state/1.0' || !object(value.books) ||
      !(value.activeBookId === null || typeof value.activeBookId === 'string') ||
      typeof value.activeReview !== 'boolean' || typeof value.activeMistakes !== 'boolean') return false;
  const validRecord = (raw: unknown, mode: PracticeSession['mode'], bookId?: string) => {
    if (!object(raw) || !Array.isArray(raw.sessions) || typeof raw.updateNotice !== 'boolean' ||
        !(raw.currentSessionId === null || typeof raw.currentSessionId === 'string')) return false;
    const sessions = raw.sessions as PracticeSession[];
    return sessions.every(s => validSession(s) && s.mode === mode && (!bookId || s.attempts.every(a => a.bookId === bookId))) &&
      new Set(sessions.map(s => s.id)).size === sessions.length &&
      (raw.currentSessionId === null || sessions.some(s => s.id === raw.currentSessionId));
  };
  return Object.entries(value.books).every(([id, raw]) => validRecord(raw, 'book', id)) &&
    validRecord(value.review, 'review') && validRecord(value.mistakes, 'mistakes');
}
export function restorePracticeState(value: string | null, library: Library): PracticeState {
  const fresh = emptyPracticeState();
  if (!value) return fresh;
  try {
    const data: unknown = JSON.parse(value);
    if (!object(data) || data.schemaVersion !== fresh.schemaVersion || !object(data.books)) return fresh;
    const restoreRecord = (raw: unknown, mode: PracticeSession['mode'], bookId?: string): PracticeBookRecord => {
      if (!object(raw) || !Array.isArray(raw.sessions)) return emptyPracticeRecord();
      const sessions = raw.sessions.filter(validSession).filter(s => s.mode === mode &&
        (!bookId || s.attempts.every(a => a.bookId === bookId)));
      if (new Set(sessions.map(s => s.id)).size !== sessions.length) return emptyPracticeRecord();
      const current = sessions.find(s => s.id === raw.currentSessionId);
      const stale = !!current && current.completedAt === null && current.attempts.some(a => {
        const owner = library.books.find(b => b.id === a.bookId);
        return owner && owner.practice?.version !== a.packVersion;
      });
      return { sessions, currentSessionId: current && !stale ? current.id : null, updateNotice: stale || raw.updateNotice === true };
    };
    for (const [id, raw] of Object.entries(data.books)) fresh.books[id] = restoreRecord(raw, 'book', id);
    fresh.review = restoreRecord(data.review, 'review');
    fresh.mistakes = restoreRecord(data.mistakes, 'mistakes');
    const available = (record: PracticeBookRecord) => !record.currentSessionId || record.sessions.find(s => s.id === record.currentSessionId)
      ?.attempts.every(a => library.books.some(b => b.id === a.bookId));
    fresh.activeMistakes = data.activeMistakes === true && !!available(fresh.mistakes);
    fresh.activeReview = !fresh.activeMistakes && data.activeReview === true && !!available(fresh.review);
    fresh.activeBookId = !fresh.activeReview && !fresh.activeMistakes && library.books.some(b => b.id === data.activeBookId && b.practice) ? data.activeBookId : null;
    return fresh;
  } catch { return fresh; }
}
export async function readPracticeState(library: Library): Promise<PracticeState> {
  const { value } = await Preferences.get({ key: PRACTICE_STORAGE_KEY });
  return restorePracticeState(value, library);
}
let writes = Promise.resolve();
export function writePracticeState(state: PracticeState): Promise<void> {
  const value = JSON.stringify(state);
  writes = writes.catch(() => undefined).then(() => Preferences.set({ key: PRACTICE_STORAGE_KEY, value }));
  return writes;
}
