import { uiBuildVersion } from '../config';
import { buildVersionLabel } from '../buildVersion';

export function BrandTitle({ title = 'bzCard', apiVersion }: { title?: string; apiVersion?: string | null }) {
  return (
    <div className="brandTitle">
      <h1>{title}</h1>
      <span className="buildVersion">
        <span className="uiBuildVersion" title="WebUIのビルドバージョン">WebUI {buildVersionLabel(uiBuildVersion)}</span>
        {apiVersion !== undefined && <> / <span className="apiBuildVersion" title="APIのビルドバージョン">API {buildVersionLabel(apiVersion)}</span></>}
      </span>
    </div>
  );
}
