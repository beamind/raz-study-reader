export type BlockKind = 'overview' | 'sentence' | 'vocabulary' | 'structure' | 'image_note' | 'question' | 'answer';
export interface SourceUnit { id: string; kind: string; text: string }
export interface LessonBlock { id: string; kind: BlockKind; title: string; sourceRefs: string[]; original: string | null; markdown: string }
export interface PageRect { x: number; y: number; width: number; height: number }
export interface OriginalPageData {
  image: string; width: number; height: number;
  hotspots: { unitId: string; rects: PageRect[] }[];
}
export interface AudioClip { unitId: string; startMs: number; endMs: number }
export interface PageAudioTrack { src: string; durationMs: number; clips: AudioClip[] }
export interface LessonPage {
  id: string; bookId: string; bookTitle: string; level: string | null;
  pdfPage: number; paperLabel: string | null; label: string; title: string;
  revision: number; status: string; sourceUnits: SourceUnit[]; blocks: LessonBlock[]; sha256: string;
  paper: OriginalPageData; audio: PageAudioTrack[];
}
export interface Book { id: string; title: string; subtitle: string; level: string | null; category: string; cover: string; pageIds: string[]; contentPath?: string; contentVersion?: string; resourceRoot?: string; practice?: import('./practice').PracticeSet }
export interface LibraryLevel { id: string; label: string; bookIds: string[] }
export interface Library { schemaVersion: string; bundleId: string; contentVersion: string; contentStatus: string; levels: LibraryLevel[]; books: Book[]; pages: Record<string, LessonPage>; pageCount?: number }
export interface Progress { pageId: string; updatedAt: number }
export type FontSize = 'standard' | 'large' | 'larger';
export interface ReaderState { schemaVersion: 1; activeBookId: string | null; progress: Record<string, Progress>; fontSize: FontSize }
export interface TextSelection {
  selectionId: string; bundleId: string; contentVersion: string;
  bookId: string; pageId: string; revision: number; selectedText: string;
  ranges: { origin: 'source' | 'lesson'; blockId: string; part: string; sourceUnitIds: string[]; start: number; end: number }[];
  sourceUnits: SourceUnit[];
}
