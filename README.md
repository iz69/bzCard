# bzCard

bzCard は、日本語名刺向けのセルフホスト型名刺管理DBです。

JPEG/PNGの名刺画像を受け取り、元画像と補正後画像を保存し、`yomitoku` でOCR、
Ollama上のローカルLLMで氏名・会社名・住所・電話番号などの項目抽出を行います。
WebUIとLINE公式アカウント連携に対応しています。

現時点では個人利用を前提にした実装です。公開環境に置く場合は、必ずHTTPSと
リバースプロキシ側の認証・アクセス制限を併用してください。

## 主な機能

- WebUIからの名刺画像アップロード
- LINE公式アカウントのWebhookからの画像登録
- 元画像、補正後画像、サムネイルの保存
- 自動切り抜き、台形補正、明るさ・コントラスト・シャープネス補正
- LINEやスマホ撮影で画像が90度回転して届いた場合の自動補正
- `yomitoku` によるOCR
- Ollama上のローカルLLMによる項目抽出
- 表面・裏面画像の管理
- 画像ハッシュによる簡易重複検出
- WebUI/API用のBearer token認証
- LIFF + 招待コードによるLINE利用登録
- SQLite保存
- 名刺ではなさそうな画像を `not_card` として停止

## 構成

- `api`: FastAPI、SQLite、バックグラウンドワーカー、OCR/LLM処理
- `ui`: React + Vite、nginx配信
- `ollama`: ローカルLLM実行環境
- `data/`: SQLite DBとアップロード画像の永続化ディレクトリ

ローカル確認用ポート:

- WebUI: `http://localhost:15174/bzcard/`
- API: `http://localhost:18081/`
- Ollama: `http://localhost:11434/`

標準では、ホスト側nginxなどで次のサブパスへproxyする想定です。

- WebUI: `/bzcard/`
- API: `/bzcard-api/`

## 公開リポジトリでの注意

このリポジトリはpublic前提です。以下はコミットしないでください。

- `.env`
- `data/`
- アップロード済み名刺画像
- SQLite DBファイル
- LINEチャネルシークレット、アクセストークン
- 実際の招待コード
- 実際のBearer token

`.gitignore` で除外しています。コミット前に確認してください。

```sh
git status --short
```

## 起動手順

`.env.example` から `.env` を作成します。

```sh
cp .env.example .env
```

最低限、`APP_API_TOKEN` は変更してください。

```env
APP_API_TOKEN=replace-with-a-long-random-token
```

コンテナを起動します。

```sh
docker compose up --build -d
```

初回はOllamaモデルを取得してください。

```sh
docker compose exec ollama ollama pull qwen2.5:7b
```

WebUIを開きます。

```text
http://localhost:15174/bzcard/
```

ログイン時は `.env` に設定した `APP_API_TOKEN` をBearer tokenとして入力します。

## 環境変数

`docker-compose.yml` は `.env` から設定値を読みます。

通常利用で最低限必要な設定:

```env
APP_API_TOKEN=replace-with-a-long-random-token
```

任意設定:

```env
LLM_MODEL=qwen2.5:7b
MAX_UPLOAD_MB=50
```

LINE連携を使う場合:

```env
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
LINE_LOGIN_CHANNEL_ID=
LINE_LIFF_ID=
LINE_LIFF_URL=
LINE_INVITE_CODES=
LINE_SESSION_TTL_HOURS=720
LINE_CARD_SCOPE=personal
```

## nginx設定例

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
    proxy_pass http://127.0.0.1:15174/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## API例

```sh
curl -H "Authorization: Bearer ${APP_API_TOKEN}" \
  -F "file=@sample.jpg" \
  "http://localhost:18081/api/cards/upload?direction=auto"
```

`direction` は次のいずれかです。

- `auto`
- `horizontal`
- `vertical`

## LINE連携

LINE公式アカウントのMessaging APIでWebhook URLに次を設定します。

```text
https://your-domain.example/bzcard-api/line/webhook
```

LIFFのEndpoint URLは次にしてください。

```text
https://your-domain.example/bzcard/liff
```

必要な設定:

- `LINE_CHANNEL_SECRET`: Messaging APIチャネルシークレット
- `LINE_CHANNEL_ACCESS_TOKEN`: Messaging APIチャネルアクセストークン
- `LINE_LOGIN_CHANNEL_ID`: LINE LoginチャネルID
- `LINE_LIFF_ID`: LIFF ID
- `LINE_LIFF_URL`: 例 `https://liff.line.me/LIFF_ID`
- `LINE_INVITE_CODES`: カンマ区切りの招待コード

LINE連携の流れ:

1. ユーザーがLIFF登録画面を開く
2. LIFFでLINE ID tokenを取得
3. APIがLINEへID tokenを検証
4. ユーザーが招待コードを入力
5. `active` になったユーザーだけ公式アカウントへ名刺画像を送信できる
6. Webhookが画像を受け取り、WebUIアップロードと同じ処理キューに投入する

`LINE_CARD_SCOPE`:

- `personal`: activeなLINEユーザーは全名刺へアクセス可能。個人利用向け。
- `owner`: LINE経由登録時の `owner_line_user_id` ごとにアクセスを分離。

## 処理の流れ

1. アップロード画像を元画像として保存
2. 補正画像を生成
   - EXIF回転補正
   - 自動切り抜き
   - 台形補正
   - 明るさ・コントラスト・シャープネス補正
3. `yomitoku` でOCR
4. `direction=auto` の場合、横書き・縦書きの読み順を判定
5. LINE/スマホ画像が90度回転して届いた可能性がある場合、回転候補を試して補正
6. 明らかに名刺ではなさそうな画像は `not_card` として停止
7. OCRテキストをOllama上のLLMに渡してJSON項目抽出
8. SQLiteへ保存

## メモリ制限

`docker-compose.yml` では、12GB程度のホストを想定して次の制限を入れています。

- `api`: `3g`
- `ui`: `128m`
- `ollama`: `6g`

OCR/LLM処理中の `docker stats` を見ながら調整してください。

## 開発時チェック

APIの構文チェック:

```sh
python3 -m compileall api/src
```

コンテナビルド:

```sh
docker compose build api ui
```

APIヘルスチェック:

```sh
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ping').status)"
```

## 補足

- 画像から文字を読む精度は主に `yomitoku` と画像品質に依存します。
- LLMはOCR後の項目抽出に使います。画像OCRそのものには使っていません。
- 低解像度の移行画像はOCR精度が大きく落ちることがあります。
- 公開環境では必ずHTTPSと外部アクセス制限を併用してください。
