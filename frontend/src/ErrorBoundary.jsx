import React from 'react';

export default class ErrorBoundary extends React.Component {
  state = {failed: false};

  static getDerivedStateFromError() {
    return {failed: true};
  }

  componentDidCatch(error, info) {
    console.error('GridGuard failed to render', error, info);
  }

  render() {
    if (this.state.failed) {
      return <main className="startup-error" role="alert">
        <h1>GridGuard could not display this view</h1>
        <p>Reload to recover. If the problem continues, restart the preview and check its terminal output.</p>
        <button onClick={() => window.location.reload()}>Reload GridGuard</button>
      </main>;
    }
    return this.props.children;
  }
}
