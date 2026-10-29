import { Capacitor, registerPlugin } from '@capacitor/core';
import { Preferences } from '@capacitor/preferences';
import type { Library, ReaderState } from './types';
import { restoreState, writeState } from './storage';
import { restorePracticeState, validPracticeBackup, writePracticeState, type PracticeBookRecord, type PracticeState } from './practice';

export const IMPORT_KEY = 'raz-reader-pending-import-v1';
export const MAX_BACKUP_BYTES = 20 * 1024 * 1024;
export interface LearningBackup {
  schemaVersion: 'raz-reader-backup/1.0'; exportedAt: string; bundleId: string;
  reader: ReaderState; practice: PracticeState;
}
const nativeFiles = registerPlugin<{
  save(options: { filename: string; data: string }): Promise<{ cancelled?: boolean }>;
  open(): Promise<{ cancelled?: boolean; data?: string }>;
}>('ReaderBackup');

export function createBackup(reader: ReaderState, practice: PracticeState, library: Library): LearningBackup {
  return { schemaVersion: 'raz-reader-backup/1.0', exportedAt: new Date().toISOString(), bundleId: library.bundleId, reader, practice };
}

function validReader(value: unknown): value is ReaderState {
  const reader = value as ReaderState | null;
  return !!reader && reader.schemaVersion === 1 && !!reader.progress && typeof reader.progress === 'object' &&
    !Array.isArray(reader.progress) && (reader.activeBookId === null || typeof reader.activeBookId === 'string') &&
    ['standard', 'large', 'larger'].includes(reader.fontSize) &&
    Object.values(reader.progress).every(item => item && typeof item.pageId === 'string' &&
      Number.isFinite(item.updatedAt) && item.updatedAt >= 0);
}

export function parseBackup(text: string): LearningBackup {
  if (new Blob([text]).size > MAX_BACKUP_BYTES) throw new Error('备份文件超过 20 MB，请选择本应用导出的记录文件。');
  let data: LearningBackup;
  try { data = JSON.parse(text); } catch { throw new Error('文件无法识别，请选择本应用导出的 JSON 备份。'); }
  if (data?.schemaVersion !== 'raz-reader-backup/1.0' || typeof data.bundleId !== 'string' ||
      typeof data.exportedAt !== 'string' || !Number.isFinite(Date.parse(data.exportedAt)) ||
      !validReader(data.reader) || !validPracticeBackup(data.practice)) {
    throw new Error('备份格式或记录不完整，当前记录没有改变。');
  }
  return data;
}

function mergeRecord(current: PracticeBookRecord, incoming: PracticeBookRecord): PracticeBookRecord {
  const sessions = new Map(current.sessions.map(s => [s.id, s]));
  for (const session of incoming.sessions) {
    const old = sessions.get(session.id);
    if (!old) sessions.set(session.id, session);
    else {
      // A later export can contain more answers from the same unfinished round.
      const submitted = (s: typeof session) => s.attempts.filter(a => a.submittedAt !== null).length;
      if (submitted(session) > submitted(old) || (submitted(session) === submitted(old) &&
          (session.completedAt ?? 0) > (old.completedAt ?? 0))) sessions.set(session.id, session);
    }
  }
  const candidates = [current.currentSessionId, incoming.currentSessionId];
  const unfinished = candidates.find(id => id && sessions.get(id)?.completedAt === null);
  return { sessions: [...sessions.values()].sort((a, b) => a.startedAt - b.startedAt),
    currentSessionId: unfinished ?? current.currentSessionId ?? incoming.currentSessionId,
    updateNotice: current.updateNotice || incoming.updateNotice };
}

export function mergeBackup(backup: LearningBackup, reader: ReaderState, practice: PracticeState, library: Library) {
  if (backup.bundleId !== library.bundleId) throw new Error('这份备份属于另一个内容库，无法导入。');
  const incomingReader = restoreState(JSON.stringify(backup.reader), library);
  const progress = { ...reader.progress };
  for (const [id, item] of Object.entries(incomingReader.progress)) {
    if (!progress[id] || item.updatedAt > progress[id].updatedAt) progress[id] = item;
  }
  const incomingPractice = restorePracticeState(JSON.stringify(backup.practice), library);
  const books = Object.assign(Object.create(null), practice.books) as PracticeState['books'];
  for (const [id, record] of Object.entries(incomingPractice.books)) {
    books[id] = books[id] ? mergeRecord(books[id], record) : record;
  }
  return {
    reader: { ...reader, progress, fontSize: incomingReader.fontSize, activeBookId: null },
    practice: { ...practice, activeBookId: null, activeReview: false, activeMistakes: false, books,
      review: mergeRecord(practice.review, incomingPractice.review), mistakes: mergeRecord(practice.mistakes, incomingPractice.mistakes) },
  };
}

// A journal makes a two-key import resumable if the app closes between writes.
export async function commitImport(reader: ReaderState, practice: PracticeState) {
  await Preferences.set({ key: IMPORT_KEY, value: JSON.stringify({ reader, practice }) });
  await writeState(reader);
  await writePracticeState(practice);
  await Preferences.remove({ key: IMPORT_KEY });
}
export async function recoverImport() {
  const { value } = await Preferences.get({ key: IMPORT_KEY });
  if (!value) return;
  const data = JSON.parse(value);
  if (!validReader(data?.reader) || !validPracticeBackup(data?.practice)) throw new Error('Invalid pending import');
  await writeState(data.reader);
  await writePracticeState(data.practice);
  await Preferences.remove({ key: IMPORT_KEY });
}

export async function saveBackup(backup: LearningBackup): Promise<boolean> {
  const data = JSON.stringify(backup, null, 2);
  if (new Blob([data]).size > MAX_BACKUP_BYTES) throw new Error('记录超过单个备份文件的 20 MB 上限，暂时无法导出。');
  const filename = `raz-learning-${backup.exportedAt.slice(0, 10)}.json`;
  if (Capacitor.getPlatform() === 'android') return !(await nativeFiles.save({ filename, data })).cancelled;
  const url = URL.createObjectURL(new Blob([data], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return true;
}
export async function openNativeBackup(): Promise<string | null> {
  const result = await nativeFiles.open();
  return result.cancelled ? null : result.data ?? null;
}
