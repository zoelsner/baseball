import React from 'react';
import { createRoot } from 'react-dom/client';
import { V2App } from './v2-pages.jsx';

window.addEventListener('error', (event) => {
  console.error('Unhandled error', event.error || event.message);
});
window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection', event.reason);
});

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error('Render error', error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{
          width: '100%', height: '100%', minHeight: '100vh', background: '#efe8dc', color: '#0f172a',
          fontFamily: '"Inter",system-ui,-apple-system,sans-serif',
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 28,
        }}>
          <div style={{
            width: '100%', maxWidth: 360, background: '#fffaf2', border: '1px solid #e2d7c6',
            borderRadius: 18, padding: 24, textAlign: 'center',
          }}>
            <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 10 }}>Something broke rendering this page</div>
            <div style={{
              fontFamily: '"JetBrains Mono","Roboto Mono",ui-monospace,monospace', fontSize: 11.5, color: '#64748b',
              marginBottom: 18, wordBreak: 'break-word',
            }}>
              {String(this.state.error && this.state.error.message || this.state.error)}
            </div>
            <button
              onClick={() => location.reload()}
              style={{
                minHeight: 44, background: '#df7042', color: '#fff', border: 'none', borderRadius: 999,
                padding: '11px 20px', fontSize: 13.5, fontWeight: 700, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const root = createRoot(document.getElementById('root'));
root.render(
  <ErrorBoundary>
    <V2App initial={{ page:'today' }}/>
  </ErrorBoundary>
);
