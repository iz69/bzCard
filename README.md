# bzCard

bzCard は、日本語名刺向けのセルフホスト型名刺管理DBです。

JPEG/PNGの名刺画像を受け取り、元画像と補正後画像を保存し、`yomitoku` でOCR、
Ollama上のローカルLLMで氏名・会社名・住所・電話番号などの項目抽出を行います。
WebUIとLINE公式アカウント連携に対応しています。

Androidネイティブアプリは開発済みですが、実際の使用感を試しつつ調整中です。

各利用者はローカルID・パスワードでログインし、自身のLINE公式アカウントを設定できます。
公開環境に置く場合は、必ずHTTPSとリバースプロキシ側のアクセス制限を併用してください。

<img width="712" height="460" alt="1" src="https://github.com/user-attachments/assets/732272e4-e999-4ca4-881b-f7b64fe66c9b" />
<img width="405" height="228" alt="2" src="https://github.com/user-attachments/assets/d76cc500-73fe-4ab1-9e20-8f5e94fa5a41" />

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
- ログインID・パスワードによるユーザー認証
- ユーザーに紐付けたLINE公式アカウントからの名刺登録
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

Ollamaはホストへポート公開せず、APIコンテナからだけ利用します。確認やモデル操作は
`docker compose exec ollama ...` で行います。

標準では、ホスト側nginxなどで次のサブパスへproxyする想定です。

- WebUI: `/bzcard/`
- API: `/bzcard-api/`

## 起動手順

`.env.example` から `.env` を作成します。

```sh
cp .env.example .env
```

利用者のログインはbzCardローカルアカウントで行います。初回にWebUIで管理者の
ログインIDと12文字以上のパスワードを作成してください。既存の名刺はその管理者へ
移行され、移行直前のDBバックアップも `data/bzcard-before-local-auth.db` に作成されます。

```env
LLM_PROVIDER=ollama
LLM_MODEL=bzcard-lfm-jp:202606
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
```

まずOllamaを起動し、既定のローカルLLMを登録します。

```sh
docker compose up -d ollama
```

`LLM_PROVIDER=ollama` の場合、初回はOllamaモデルを取得してください。既定の
`bzcard-lfm-jp:202606` は、公式GGUFから作成するローカルモデルです。

```sh
curl -fL https://huggingface.co/LiquidAI/LFM2.5-1.2B-JP-202606-GGUF/resolve/main/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf -o /tmp/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf
docker compose exec ollama mkdir -p /root/.ollama/import
docker cp /tmp/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf bzcard-ollama:/root/.ollama/import/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf
docker compose exec ollama sh -lc 'printf "FROM /root/.ollama/import/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf\\nPARAMETER num_ctx 4096\\n" > /root/.ollama/import/Modelfile.lfm-jp'
docker compose exec ollama ollama create bzcard-lfm-jp:202606 -f /root/.ollama/import/Modelfile.lfm-jp
rm /tmp/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf
```

続いて、APIとWebUIを含む全コンテナを起動します。

```sh
docker compose up --build -d
```

Gemini APIを使う場合は `.env` で次のように設定します。

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.5-flash
```

WebUIを開きます。

```text
http://localhost:15174/bzcard/
```

初回は管理者IDとパスワードを作成します。以後は、WebUIでは同じサーバのAPI URL、
ログインID、パスワードでログインします。Androidも同じ認証情報を使います。

## 環境変数

`docker-compose.yml` は `.env` から設定値を読みます。

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

### LLMモデルの切替

現在の既定は、日本語特化のローカルモデル `bzcard-lfm-jp:202606` です。従来の
`qwen2.5:7b` は削除せず残せるため、`.env` の `LLM_MODEL` を次の値に戻して
`docker compose up -d --force-recreate api` を実行すれば復帰できます。

```env
LLM_MODEL=qwen2.5:7b
```

SQLiteデータベースはホスト側の `data/bzcard.db` に保存されます。画像と
`data/line-credentials.key` も同じ `data/` 配下にあるため、バックアップ時は
ディレクトリごと保管してください。

LINE公式アカウントの接続情報は、環境変数ではなくログイン後の「LINE設定」画面で、
利用者ごとに保存します。設定する値はMessaging APIのチャネルシークレット／
チャネルアクセストークン、LINE LoginチャネルID、LIFF URLです。

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

接続情報は暗号化してDBに保存され、画面から再表示されません。暗号化鍵を明示的に
`LINE_CREDENTIALS_ENCRYPTION_KEY` として設定する場合は、DBバックアップと同じ安全な
場所へ保管してください。未設定の場合も `data/line-credentials.key` が自動作成されるため、
このファイルを `data/bzcard.db` と必ず一緒にバックアップしてください。

```env
LINE_CREDENTIALS_ENCRYPTION_KEY=
SESSION_TTL_HOURS=720
```

LINE公式アカウント連携のWebhook URL:

```env
https://your-domain.example/bzcard-api/line/webhook
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

通常APIは、ログイン後に発行される利用者セッショントークンをBearerとして指定します。
共通のAPIトークンは使用しません。

```sh
curl -H "Authorization: Bearer ${BZCARD_SESSION_TOKEN}" \
  -F "file=@sample.jpg" \
  "http://localhost:18081/api/cards/upload?direction=auto"
```

`direction` は次のいずれかです。

- `auto`
- `horizontal`
- `vertical`

Androidなどのクライアントから、実際に名刺処理で使用するOCR/LLMのバージョンを確認できます。
利用者セッションによるBearer認証が必要です。

```sh
curl -H "Authorization: Bearer ${BZCARD_SESSION_TOKEN}" \
  "http://localhost:18081/api/system/versions"
```

レスポンスには `yomitoku` の導入済みバージョン、OCRのlite設定・デバイス、
LLMプロバイダーとモデル名を含みます。Ollama利用時は、Ollamaサーバー版、モデルの
digest、パラメーター数・量子化方式などのモデル詳細も返します。Ollamaが停止中の場合も
HTTP 200で `llm.status: "unavailable"` を返します。

## LINE連携

LINE公式アカウントのMessaging APIでWebhook URLに次を設定します。

```text
https://your-domain.example/bzcard-api/line/webhook
```

公式LINEから名刺検索・名刺画像登録を行えるのは、そのbzCard利用者に紐付けたLINEアカウント
だけです。LINE Loginチャネルを変更した場合は安全のため紐付けが解除されるため、設定画面から
新しい紐付けURLを発行して再連携してください。

LINE連携の流れ:

1. WebUIでローカル管理者アカウントを作成する
2. WebUIの「LINE設定」で、利用者自身のMessaging API／LINE Login設定を保存する
3. Webhookが画像を受け取り、紐付け済みローカルユーザーの処理キューに投入する

すべての名刺はbzCardローカルユーザーに紐付きます。別ユーザーの名刺は、一覧、
検索、詳細、画像、更新、削除、ジョブ取得のいずれからも取得できません。

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

`docker-compose.yml` では、LFM2.5-1.2B-JP のQ4量子化モデルを使う8GB程度のホストを
想定して次の制限を入れています。

- `api`: `3g`
- `ui`: `128m`
- `ollama`: `2g`

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

## おまけ

- myBridge からスキャン済み画像をダウンロードするやつ
https://github.com/iz69/mybridge_capture

## 📜 License
Copyright (c) 2025 Kuromaru Soft
- **Free for personal and non-commercial use.**
- **Commercial use is prohibited** without prior permission (this includes business use, resale, or integration into paid services).
- For commercial inquiries, please contact.

本ソフトウェアは、個人または非商用目的に限り、無償で使用・改変・再配布を許可します。<br/>
商用目的（直接・間接を問わず利益を得る目的）での利用は禁止します。<br/>

以下の行為を「商用利用」とし、事前の許諾なしに行うことを禁止します。<br/>
- 有償での提供、販売、再販
- 有料サービス・課金機能への組み込み
- 企業・組織での業務利用（社内利用を含む）
- 本ソフトウェアを利用したホスティング/運用代行の提供

商用利用を希望する場合はご連絡ください。<br/>
本ソフトウェアは現状のまま提供され、いかなる保証もありません。<br/>
作者は本ソフトウェアの利用により生じた損害について責任を負いません。<br/>

## 💖 Support & Donation
GitHub Sponsors: github.com/sponsors/iz69
