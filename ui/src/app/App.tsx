import { lazy, Suspense } from 'react';
import { isLiffLocation } from '../deployment';
import { uiBasePath } from '../shared/config';
import { getDeviceMode } from './deviceMode';
import { useSession } from './useSession';

const DesktopApp = lazy(() => import('../desktop/DesktopApp'));
const MobileApp = lazy(() => import('../mobile/MobileApp'));
const LiffApp = lazy(() => import('../liff/LiffApp'));
// Choose once at startup. Rotation, resizing and the on-screen keyboard only
// change the active layout; they do not unmount the UI or discard edits.
const mode = getDeviceMode();

export default function App() {
  const { session, saveSession } = useSession();
  const liff = isLiffLocation(window.location.pathname, window.location.search, uiBasePath);
  const Workspace = mode === 'mobile' ? MobileApp : DesktopApp;
  return <Suspense fallback={<div role="status">読み込み中…</div>}>
    {liff ? <LiffApp /> : <Workspace key={`${session.apiBase}:${session.token}`} session={session} saveSession={saveSession} />}
  </Suspense>;
}
