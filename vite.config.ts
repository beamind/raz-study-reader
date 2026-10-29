import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { createReadStream, statSync } from 'node:fs';
import path from 'node:path';

const contentRoot = path.resolve('content');
const contentTypes: Record<string, string> = {
  '.json': 'application/json; charset=utf-8', '.webp': 'image/webp', '.mp3': 'audio/mpeg',
};

/** Serve reader assets with HTTP Range support so audio can seek to sentence starts. */
function serveReaderContent(root: string, request: import('node:http').IncomingMessage,
                            response: import('node:http').ServerResponse,
                            next: () => void): void {
  try {
    const relative = decodeURIComponent((request.url ?? '').split('?')[0]).replace(/^\/+/, '');
    const file = path.resolve(root, relative);
    if (!file.startsWith(root + path.sep) || relative.split('/').includes('_source') || !statSync(file).isFile()) {
      next(); return;
    }
    const size = statSync(file).size;
    response.setHeader('Content-Type', contentTypes[path.extname(file)] ?? 'application/octet-stream');
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Accept-Ranges', 'bytes');

    const range = request.headers.range;
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range);
      let start: number, end: number;
      if (match && match[1] !== '' && match[2] !== '') {
        start = parseInt(match[1], 10);
        end = Math.min(parseInt(match[2], 10), size - 1);
      } else if (match && match[1] !== '') {
        start = parseInt(match[1], 10);
        end = size - 1;
      } else if (match && match[2] !== '') {
        start = Math.max(0, size - parseInt(match[2], 10));
        end = size - 1;
      } else {
        next(); return;
      }
      if (start > end || start >= size) {
        response.writeHead(416, { 'Content-Range': `bytes */${size}` });
        response.end();
        return;
      }
      response.writeHead(206, {
        'Content-Range': `bytes ${start}-${end}/${size}`,
        'Content-Length': end - start + 1,
      });
      createReadStream(file, { start, end }).pipe(response);
      return;
    }

    response.writeHead(200, { 'Content-Length': size });
    createReadStream(file).pipe(response);
  } catch {
    next();
  }
}

function readerContent(): Plugin {
  return {
    name: 'reader-content',
    configureServer(server) {
      server.middlewares.use('/content', (request, response, next) => serveReaderContent(contentRoot, request, response, next));
    },
    configurePreviewServer(server) {
      server.middlewares.use('/content', (request, response, next) => serveReaderContent(path.resolve('dist/content'), request, response, next));
    },
  };
}

export default defineConfig({
  plugins: [react(), readerContent()],
  base: './',
  server: {
    port: 5173, strictPort: true,
    // Local source corpora and verification outputs are not application modules.
    watch: { ignored: ['**/content/**/_source/**', '**/.local/**', '**/artifacts/**', '**/test-results/**'] },
  },
  preview: { port: 4173, strictPort: true },
  build: { target: 'chrome87' },
});
