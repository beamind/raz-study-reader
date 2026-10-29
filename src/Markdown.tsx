import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function SafeMarkdown({ text }: { text: string }) {
  return <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    // Bundled lessons have no linked assets. Labels remain readable without opening a browser.
    a: ({ children }) => <span>{children}</span>,
    img: ({ alt }) => alt ? <span className="image-description">{alt}</span> : null,
    h1: ({ children }) => <h3>{children}</h3>,
    h2: ({ children }) => <h3>{children}</h3>,
  }}>{text}</Markdown>;
}
