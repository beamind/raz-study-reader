import type { Library, LessonPage, TextSelection } from './types';

function offsetWithin(element: Element, node: Node, offset: number): number {
  const prefix = document.createRange();
  prefix.selectNodeContents(element);
  prefix.setEnd(node, offset);
  return prefix.toString().length;
}

/** DOM-text UTF-16 offsets, scoped by stable source/block IDs, never by a word search. */
export function captureSelection(root: HTMLElement, page: LessonPage, library: Library): TextSelection | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount || !selection.toString().trim()) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const ranges: TextSelection['ranges'] = [];
  for (const element of root.querySelectorAll<HTMLElement>('[data-selectable]')) {
    // A range may cross a collapsed answer in the DOM; it is not part of the visible selection.
    if (!element.getClientRects().length) continue;
    const collapsed = element.closest('details:not([open])');
    if (collapsed && !collapsed.querySelector(':scope > summary')?.contains(element)) continue;
    if (!range.intersectsNode(element)) continue;
    const start = element.contains(range.startContainer) ? offsetWithin(element, range.startContainer, range.startOffset) : 0;
    const end = element.contains(range.endContainer) ? offsetWithin(element, range.endContainer, range.endOffset) : element.textContent?.length ?? 0;
    if (start >= end) continue;
    const blockId = element.closest<HTMLElement>('[data-block-id]')?.dataset.blockId;
    if (!blockId) continue;
    ranges.push({
      origin: element.dataset.selectable === 'source' ? 'source' : 'lesson',
      blockId, part: element.dataset.selectionPart ?? (element.dataset.selectable === 'source' ? 'original' : 'body'),
      sourceUnitIds: (element.dataset.sourceRefs || '').split(' ').filter(Boolean), start, end,
    });
  }
  if (!ranges.length) return null;
  const refs = new Set(ranges.flatMap(r => r.sourceUnitIds));
  return {
    selectionId: crypto.randomUUID(), bundleId: library.bundleId, contentVersion: library.contentVersion,
    bookId: page.bookId, pageId: page.id, revision: page.revision,
    selectedText: selection.toString(), ranges,
    sourceUnits: page.sourceUnits.filter(unit => refs.has(unit.id)),
  };
}
