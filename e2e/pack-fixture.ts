import path from 'node:path';
import { readRuntimeLibrary } from '../tools/read_runtime_library.mjs';

// Generated only from tools/synthetic_content.py; no private content dependency.
export const library = readRuntimeLibrary(path.resolve('artifacts/fixture-content/content'));
