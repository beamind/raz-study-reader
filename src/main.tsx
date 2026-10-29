import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { ContentProvider, loadLibrary } from './content';
import './styles.css';
import { Capacitor } from '@capacitor/core';

if (import.meta.env.PROD && import.meta.env.MODE !== 'bundled' && !Capacitor.isNativePlatform() && 'serviceWorker' in navigator) {
  void navigator.serviceWorker.register(new URL('reader-sw.js', document.baseURI), { scope: './' })
    .catch(error => console.error('Offline reader shell could not be prepared', error));
}

class ErrorBoundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <div className="loading-screen"><h1>这一页暂时没能打开</h1><p>阅读进度已经保留，请重新打开试试。</p><button className="primary" onClick={() => location.reload()}>重新打开</button></div> : this.props.children;
  }
}

const root = createRoot(document.getElementById('root')!);

async function start() {
  root.render(<div className="loading-screen" role="status"><h1>RAZ 小小精读</h1><p>正在打开你的书架…</p></div>);
  try {
    const library = await loadLibrary();
    root.render(<React.StrictMode><ErrorBoundary><ContentProvider initial={library}><App/></ContentProvider></ErrorBoundary></React.StrictMode>);
  } catch (error) {
    console.error(error);
    root.render(<div className="loading-screen" role="alert"><h1>阅读内容暂时没能打开</h1><p>请重试。原有资源包和学习记录已保留。</p><button className="primary" onClick={() => void start()}>重新打开</button></div>);
  }
}

void start();
