import React from 'react';
import {createRoot} from 'react-dom/client';
import ErrorBoundary from './ErrorBoundary.jsx';

async function dashboard() {
  if (window.location.hash === '#storm') {
    await import('./styles.css');
    return (await import('./App.jsx')).default;
  }
  if (window.location.hash === '#demo') {
    return (await import('./LegacyApp.jsx')).default;
  }
  return (await import('./GridExplorer.jsx')).default;
}

dashboard().then(App => {
  createRoot(document.getElementById('root')).render(
    <ErrorBoundary><App /></ErrorBoundary>,
  );
});
