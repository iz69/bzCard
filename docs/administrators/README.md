# 設定・運用ガイド

サーバーを導入・公開・管理する人向けのガイドです。
初めて導入する場合は [Dockerイメージでの導入](installation.md) から進めてください。
名刺の操作と利用者自身のLINE設定は [利用ガイド](../users/README.md)、
ソースの変更・ビルド・テストは [開発ガイド](../developers/README.md) を参照してください。

このガイドのコマンドは、配布用の `docker-compose.yml` と `.env` を置いたディレクトリで実行します。
ソースビルド構成を運用する場合は、各Composeコマンドに
`-f docker-compose.develop.yml` を付けてください。

## 初回の管理者作成

利用者のログインはbzCardローカルアカウントで行います。初回にWebUIで管理者の
ログインIDと12文字以上のパスワードを作成してください。既存の名刺はその管理者へ
移行され、移行直前のDBバックアップもデータ保存先の `bzcard-before-local-auth.db` に作成されます。

## 環境変数

`docker-compose.yml` と `docker-compose.develop.yml` は `.env` から設定値を読みます。
公開パスはビルド時ではなく、コンテナ起動時に反映されます。

```env
UI_BASE_PATH=/bzcard/
API_BASE_PATH=/bzcard-api
```

`UI_BASE_PATH` は末尾に `/` を付けた絶対パスです。APIをルートで公開する場合は
`API_BASE_PATH=/` とします。UI・APIの設定と外側のプロキシ設定を合わせてください。
設定変更後は `docker compose up -d` でコンテナを再作成します。

OCR/LLMの任意設定:

```env
LLM_PROVIDER=ollama
LLM_MODEL=bzcard-lfm-jp:202606
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
MAX_UPLOAD_MB=50
```

`LLM_PROVIDER` は次のいずれかです。

- `ollama`: ローカルOllamaの `LLM_MODEL` を使います。
- `gemini`: Gemini APIの `GEMINI_MODEL` を使います。`GEMINI_API_KEY` が必要です。

Gemini APIを使う場合の設定例:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash
```

### LLMモデルの切替

現在の既定は、日本語特化のローカルモデル `bzcard-lfm-jp:202606` です。従来の
`qwen2.5:7b` は削除せず残せるため、`.env` の `LLM_MODEL` を次の値に戻して
`docker compose up -d --force-recreate api` を実行すれば復帰できます。

```env
LLM_MODEL=qwen2.5:7b
```

SQLiteデータベースは既定でホスト側の `data/bzcard.db` に保存されます。画像と
`data/line-credentials.key` も同じデータ保存先にあるため、バックアップ時は
ディレクトリごと保管してください。

利用者ごとのLINE公式アカウントの接続情報は、環境変数ではなく
[「LINE設定」画面](../users/README.md#line連携) で保存します。
OCRの文字認識モデルは `OCR_RECOGNIZER_MODEL` で指定します。
既定値は `parseq-large-v4_1` です。

## 利用者モード

`.env` の `MULTI_USER_ENABLED` で、コンテナ再作成時に利用者モードを切り替えられます。

```env
# 管理者1名だけで使う（既定値）
MULTI_USER_ENABLED=false

# 管理者と一般利用者を使う
MULTI_USER_ENABLED=true
```

`false` では管理者だけがログイン・名刺操作・公式LINE連携を利用できます。一般利用者の
アカウント、名刺、公式LINE設定は削除されず、`true` に戻すと再び利用できます。既存の
一般利用者セッションも、このモードではAPI利用を拒否されます。

### 利用者管理

マルチユーザーモードの管理者は、WebUIのアカウントメニュー「利用者管理」で
一般ユーザーの追加・停止・再開・削除ができます。停止すると新規ログインと既存セッションを
無効化し、LINEからの検索・登録も停止します。データは保持し、再開後は再ログインが必要です。

削除には対象ログインIDの再入力が必要です。対象ユーザーの名刺・表裏画像・処理ジョブ・
セッション・LINE接続設定・連携要求・接続の履歴を削除します。ほかのユーザーが参照する
インポート履歴は保持します。管理者アカウントと既定のLINE接続の所有者は停止・削除できません。
進行中のリクエストやOCR処理がある場合は完了後の再試行が必要です。別ユーザーからの
画像参照や不正な保存パスを検出した場合は、削除を中止します。

DBの削除はトランザクションで行います。画像削除に失敗した場合は成功扱いにせず、
再試行のためユーザーとDB情報を保持します（すでに削除された対象ユーザーの画像は戻りません）。
既存バックアップと共通の暗号化鍵はこの操作の対象外です。バックアップから復元すると
削除済みデータも復元される可能性があります。


## LINE認証情報の保管

接続情報は暗号化してDBに保存され、画面から再表示されません。暗号化鍵を明示的に
`LINE_CREDENTIALS_ENCRYPTION_KEY` として設定する場合は、DBバックアップと同じ安全な
場所へ保管してください。未設定の場合も `data/line-credentials.key` が自動作成されるため、
このファイルをDBと必ず一緒にバックアップしてください。保存先を変更した場合は、
そのデータ保存先の `line-credentials.key` を保管します。

```env
LINE_CREDENTIALS_ENCRYPTION_KEY=
SESSION_TTL_HOURS=720
```

## nginx設定例

公開環境では必ずHTTPSとリバースプロキシ側のアクセス制限を併用してください。

ホスト側nginxなどでサブパスへproxyします。画像アップロードのため、API側は
`client_max_body_size` を大きめにしてください。

```nginx
location /bzcard-api/ {
    client_max_body_size 60m;
    proxy_pass http://127.0.0.1:18081/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /bzcard/ {
    proxy_pass http://127.0.0.1:15174;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

UI側の `proxy_pass` には末尾の `/` を付けず、公開パスを保持します。
API側は公開プレフィックスを除いて転送します。UIコンテナはAPIを中継しないため、
プロキシを使わずローカルで確認する場合は、ログイン画面のAPI URLに
`http://localhost:18081` を指定してください。

任意のサブパス、ルートでの公開、LINEの公開URLについては
[導入ガイドの公開パス設定](installation.md#任意のサブパス) を参照してください。

## 更新と既存配置からの移行

`latest` を使う場合は、そのまま最新イメージを取得する。
バージョンを固定している場合は、`docker-compose.yml` の対象サービスの `image` タグを
公開済みの更新先バージョンに変更して取得する。

```sh
docker compose pull
docker compose stop api
```

APIを停止した状態で、DB・画像・暗号化鍵を含むデータ保存先をバックアップする。
その後、新しいイメージで起動する。

```sh
docker compose up -d
```

ソースビルド版から移行する場合は、バックアップ後に
`docker compose -f docker-compose.develop.yml down` で従来のコンテナを停止・削除し、
同じ `data/`・`ollama/`・暗号化鍵を指定して配布用Composeを起動する。
Composeのプロジェクト名が異なる配置では、その元のプロジェクトを指定して停止する。
既存データがあるため、永続ボリュームを削除するオプションは付けない。

## バックアップとDB移行

データ保存先の既定値は `./data` です。配布用Composeでは `BZCARD_DATA_DIR` で変更できます。
更新前にAPIを停止し、DB・画像・暗号化鍵を含むデータ保存先全体をバックアップしてください。
`.env` とOllamaのモデル保存先（既定は `./ollama`）も、復旧に必要な設定・モデルとして保管します。

暗号化鍵を `LINE_CREDENTIALS_ENCRYPTION_KEY` で指定している場合は、その値も必要です。
未指定の場合は、データ保存先の `line-credentials.key` をDBと一緒に保管します。

旧DBの画像保存先移行では、初回起動時に `bzcard-before-image-storage-v1.db` を
データ保存先に作成します。再起動でバックアップを上書きしません。
自動作成するDBバックアップには、画像ファイル自体は含まれません。
旧コードへ戻す場合は、移行後DBをそのまま使わず、対応する更新前のDBと画像を復元してください。

人物一覧の要約は、更新後の初回起動で既存名刺から一度だけ作成します。
件数に応じてAPIの起動に時間がかかります。再起動時は作成し直しません。

## 処理の復旧

- 再起動時には処理中のOCRジョブを復旧し、初回を含む最大3回で失敗を確定します。
  `error` の名刺は画面から再スキャンできます。
- 画像変更・削除・再処理が競合した場合は、処理の完了後に再試行してください。
  手動項目の保存は処理中も可能ですが、解析完了時に結果で上書きされます。
- 裏面画像は検証が成功してから参照を切り替えます。
  検証失敗や別名刺との重複で以前の画像を失わないようにします。
- LINEイベントは保存後にバックグラウンドで処理し、一時失敗・再配送・再起動に対応します。
  最大3回で失敗した場合は画像を再送してください。返信失敗で登録済みの名刺は取り消しません。

ワーカーと画像操作のロックは、同じローカルデータ領域を共有するLinuxプロセス向けです。
SQLiteと画像を複数ホストへ分散する構成は対象にしていません。
内部の移行・排他制御は [名刺処理と補正履歴](../developers/processing.md#db移行処理復旧) に記載しています。

## メモリ制限

両方のCompose構成では、LFM2.5-1.2B-JP のQ4量子化モデルを使う8GB程度のホストを
想定して次の制限を入れています。

- `api`: `3g`
- `ui`: `128m`
- `kana`: `2500m`
- `ollama`: `2g`

OCR/LLM処理中の `docker stats` を見ながら調整してください。

## 稼働確認

APIヘルスチェック:

```sh
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ping').status)"
```

成功時は `200` を表示します。
