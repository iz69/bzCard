# bzcard

名刺画像をアップロードし、サーバ側で `yomitoku` OCR とローカルLLM抽出を行う名刺管理DBの初期実装です。

## 初期構成

- API: FastAPI + SQLite + background worker
- OCR: yomitoku
- LLM: Ollama
- UI: React + Vite + nginx
- 永続化: `./data:/data`

## 起動

```sh
docker compose up --build
```

初回はOllamaモデルを取得してください。

```sh
docker compose exec ollama ollama pull qwen2.5:7b
```

`docker-compose.yml` の `APP_API_TOKEN` は必ず変更してください。

## URL

ホストnginxからは以下のようにサブパスへproxyする想定です。

- WebUI: `/bzcard/`
- API: `/bzcard-api/`

ローカル確認用の公開ポート:

- WebUI: `http://localhost:15174/`
- API: `http://localhost:18081/`

## nginx例

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
curl -H "Authorization: Bearer change-me" \
  -F "file=@sample.jpg" \
  http://localhost:18081/api/cards/upload
```

## LINE連携

LINE公式アカウントのMessaging APIでWebhook URLに次を設定します。

```text
https://your-domain.example/bzcard-api/line/webhook
```

APIコンテナには以下を設定してください。

- `LINE_CHANNEL_SECRET`: Messaging APIチャネルシークレット
- `LINE_CHANNEL_ACCESS_TOKEN`: Messaging APIチャネルアクセストークン
- `LINE_LOGIN_CHANNEL_ID`: LIFF/LINE LoginのChannel ID
- `LINE_LIFF_URL`: 利用登録と処理後の確認画面URL。例: `https://liff.line.me/LIFF_ID`
- `LINE_LIFF_ID`: UIビルド用のLIFF ID。`LINE_LIFF_URL` からも推定しますが、明示設定を推奨
- `LINE_INVITE_CODES`: カンマ区切りの招待コード。例: `team-2026,another-code`
- `LINE_SESSION_TTL_HOURS`: LIFF用セッションの有効時間。デフォルト720時間
- `LINE_CARD_SCOPE`: `personal` または `owner`。デフォルトは個人利用向けの `personal`

LINEから画像メッセージを受けると、既存の名刺登録キューに投入します。WebhookはLINE署名を検証し、同じイベントの再送は `webhookEventId` で二重処理しません。

LINE入口は既存のWebUI/Bearer APIとは別認証です。LIFFから `/line/auth/login` にLINEのID tokenと招待コードを送ると、サーバ側でID tokenを検証し、招待コードが一致したユーザーだけ `active` にします。Webhookは `active` なLINEユーザーの画像だけ受け付けます。

`LINE_CARD_SCOPE=personal` では、activeなLINEユーザーはWebUI登録分を含む全名刺を検索・確認・編集できます。将来マルチユーザー化する場合は `LINE_CARD_SCOPE=owner` に切り替えると、LINE経由登録時に付与した `owner_line_user_id` で名刺アクセスを分離します。

LIFFのEndpoint URLは次にしてください。

```text
https://your-domain.example/bzcard/liff
```

```http
POST /line/auth/login
Content-Type: application/json

{
  "id_token": "LIFFで取得したID token",
  "invite_code": "招待コード"
}
```
