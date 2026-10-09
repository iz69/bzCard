# 開発ガイド

ソースを変更・ビルド・検証する人向けのガイドです。
公開済みのイメージでサーバーを導入する場合は [導入ガイド](../administrators/installation.md)、
サーバー設定やバックアップは [設定・運用ガイド](../administrators/README.md) を参照してください。

## 仕様と検証記録

- [API利用と互換性](api.md)
- [名刺処理と補正履歴](processing.md)
- [人物一覧の取得](contact-list.md)
- [氏名・フリガナの判断と回帰検証](person-name-reading.md)
- [Dockerイメージの公開と検証](container-release.md)
- [実装チェックリストと検証記録](implementation-checklist.md)

このガイドのコマンドは、特に指定がなければリポジトリのルートから実行します。

## 構成

- `api`: FastAPI、SQLite、バックグラウンドワーカー、OCR/LLM処理
- `ui`: React + Vite、nginx配信
- `ollama`: ローカルLLM実行環境
- `kana`: 氏名の読みを推論するローカルPyTorchサービス（1.9o）
- `data/`: SQLite DBとアップロード画像の永続化ディレクトリ

WebUIのソースは次の構成です。

- `ui/src/app/`: 起動時のPC・スマートフォン判定、LIFF優先の画面選択、セッション同期
- `ui/src/desktop/`: 従来のPC用画面。人物の自動選択、仮想テーブル、ページ取得処理を維持
- `ui/src/mobile/`: 一覧・詳細・追加・設定、モバイル用のページ取得・編集・画面遷移
- `ui/src/liff/`: 従来のLINE認証・LIFF画面
- `ui/src/shared/`: 共通のAPI通信、型、編集項目、認証画面、画像、タグ、設定ダイアログ、補正履歴

PC・LIFFの既存CSSは `ui/src/styles.css`、モバイルCSSは `ui/src/mobile/mobile.module.css` に置きます。
モバイルのスタイルはモバイルのルート内に限定し、PC版のCSSを変更せずに調整できます。
端末判定は起動時に固定し、PCの狭いウィンドウ・タッチ対応PC・タブレットをPC版として扱います。
モバイルの画面遷移には同じURLの `history.state` を使い、再ログイン時には遷移の範囲を作り直します。
認証・API・公開パス・保存済みセッションの形式は共通です。各画面は遅延読み込みします。

ローカル確認用ポート:

- WebUI: `http://localhost:15174/bzcard/`
- API: `http://localhost:18081/`

Ollamaとkanaはホストへポート公開せず、APIコンテナからだけ利用します。Ollamaの確認やモデル操作は
`docker compose exec ollama ...` で行います。

標準では、ホスト側nginxなどで次のサブパスへproxyする想定です。

- WebUI: `/bzcard/`
- API: `/bzcard-api/`

## ソースからの起動手順

Git、Docker、Docker Compose（`docker compose` コマンド）を用意してください。
この手順ではAPI・WebUI・kanaを配置先でソースからビルドします。
`docker-compose.develop.yml` だけでなく、リポジトリ全体が必要です。
Ollamaは公開イメージを取得し、モデルは別途登録します。

初回はリポジトリをcloneし、取得したディレクトリへ移動します。
以降のコマンドは、このディレクトリで実行してください。

```sh
git clone https://github.com/iz69/bzCard.git bzcard
cd bzcard
export COMPOSE_FILE=docker-compose.develop.yml
```

この節では `COMPOSE_FILE` でソースビルド用の構成を選びます。
新しいシェルでは再指定するか、各コマンドに `-f docker-compose.develop.yml` を付けてください。

`.env.example` から `.env` を作成します。

```sh
cp .env.example .env
```

[環境変数](../administrators/README.md#環境変数) と
[利用者モード](../administrators/README.md#利用者モード) は、配布版と共通です。

```env
LLM_PROVIDER=ollama
LLM_MODEL=bzcard-lfm-jp:202606
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
```

まずOllamaを起動します。

```sh
docker compose up -d ollama
```

[導入ガイドのモデル準備](../administrators/installation.md#モデルの準備) を実行します。
このシェルでは `COMPOSE_FILE=docker-compose.develop.yml` を維持し、
モデル保存先には `./data` を使ってください。

続いて、APIとWebUIを含む全コンテナを起動します。

```sh
docker compose up --build -d
```

WebUIのURLは `http://localhost:15174/bzcard/` です。
初回の [管理者作成](../administrators/README.md#初回の管理者作成) と
[ログイン](../users/README.md#ログイン) を行います。
プロキシなしの場合、ログイン画面のAPI URLは `http://localhost:18081` を指定してください。
Gemini APIを使う場合は [LLM設定](../administrators/README.md#環境変数) を参照してください。

## ソースからの更新手順

インストール済みのリポジトリで、ソースを更新してAPIとWebUIを再ビルドします。
標準の `main` ブランチを使っている場合は、次を実行してください。
ローカルで追跡対象のファイルを変更している場合は、変更内容を確認してから更新してください。

```sh
git pull --ff-only
docker compose -f docker-compose.develop.yml build api ui kana
```

新しいコンテナを起動する前にAPIを停止し、DB・画像・暗号化鍵を含む `data/` 全体を
別途バックアップしてください。詳細は [バックアップとDB移行](../administrators/README.md#バックアップとdb移行) を参照してください。

```sh
docker compose -f docker-compose.develop.yml stop api
```

バックアップ後、ビルドしたイメージでコンテナを更新します。

```sh
docker compose -f docker-compose.develop.yml up -d
```

初回設定済みの `.env`、`data/`、`ollama/` は引き続き使います。kanaモデルの重みをまだ配置していない環境では、[モデルの準備](../administrators/installation.md#モデルの準備)を先に実行してください。
モデルを変更しない場合、Ollamaモデルの再取得・登録は不要です。
通常の再起動ではclone・pull・checkoutは不要です。ソースを更新したときに再ビルドし、
特定のタグやコミットへ切り替える場合だけcheckoutで対象を選んでから再ビルドしてください。

## 開発時チェック

API回帰テスト（本番データをマウントせずに実行）:

```sh
docker build -t bzcard-api:distribution-test api
docker build -f api/Dockerfile.test -t bzcard-api:test api
docker run --rm --network none -v "$PWD/api:/review:ro" -w /review \
  -e DATA_DIR=/tmp/bzcard-test -e PYTHONPATH=/review \
  bzcard-api:test python -B -m unittest discover -p 'test_*.py'
```

テスト専用イメージには `requirements-test.txt` の依存を導入します。
APIテストの実行時はネットワークを無効にします。

UIの依存は固定し、Dockerビルドは `npm ci` と型検査を使います。

```sh
cd ui
npm ci
npm run typecheck
npm test
npm run test:runtime
npm run build
cd ..
```

ブラウザ回帰テストは、検証用UIとChromiumを用意して実行します。
API通信はすべて架空のレスポンスに置き換えます。

公開パスの異なる配置を検証するときは、UIとAPIのURL設定をその配置に合わせます。
すでに起動したChromiumへ接続する場合は、BROWSER_EXECUTABLEの代わりに
BROWSER_CDP_URLを指定できます。

```sh
cd ui
BZCARD_TEST_UI_URL=http://127.0.0.1:15175/bzcard/ \
BZCARD_TEST_API_BASE_PATH=/bzcard-api \
BROWSER_EXECUTABLE=/path/to/chromium npm run test:browser
cd ..
```

`test:browser` はPC・LIFF・モバイルの回帰テストを実行します。
モバイルだけを検証する場合は、同じ環境変数で `npm run test:mobile` を実行してください。
モバイルの検証にはページ追加取得・409からの復旧・検索・処理完了への追従・未保存編集・
保存中の追加入力・戻る操作・画面回転・画像登録・裏面追加・回転・再解析・設定・利用者切替を含みます。
撮影ボタンの属性と登録通信は検証しますが、カメラの起動や実際の撮影は実機確認が必要です。

同じDockerイメージを6種類の公開パスで検証する場合:

```sh
# リポジトリのルートでビルド
docker build -t bzcard-ui:distribution-test ui
cd ui
BROWSER_EXECUTABLE=/path/to/chromium npm run test:container
cd ..
```

実LLMの補正参照評価は `api/evaluate_feedback.py` を実行します。常に一時DBを使い、
同じ人物・別利用者・印刷された読みとの矛盾を比較します。OCRの認識率を測る評価ではありません。
検証済みの条件と結果は [実装チェックリスト](implementation-checklist.md) に記載します。

APIの構文チェック:

```sh
python3 -m compileall api/src
```

コンテナビルド:

```sh
docker compose -f docker-compose.develop.yml build api ui
```

APIヘルスチェック:

```sh
docker compose -f docker-compose.develop.yml exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ping').status)"
```
