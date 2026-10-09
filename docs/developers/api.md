# API利用と互換性

クライアントの実装・連携を行う開発者向けのガイドです。
公開パスの設定は [設定・運用ガイド](../administrators/README.md#環境変数) を参照してください。
以下の例は、ローカル確認用のAPI URLを使います。

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

レスポンスの `api.version` はAPIイメージにビルド時に設定したリリースタグ（例: `v0.9.3`）で、
ローカル開発ビルドでは `dev` です。既存のOCR/LLM/kana情報に追加した項目で、認証方式は同じです。
レスポンスにはOCRエンジンの導入済みバージョン、認識モデル・デバイス、
LLMプロバイダーとモデル名を含みます。Ollama利用時は、Ollamaサーバー版、モデルの
digest、パラメーター数・量子化方式などのモデル詳細も返します。Ollamaが停止中の場合も
HTTP 200で `llm.status: "unavailable"` を返します。

## 解析・補正履歴API

所有者認証が必要な追加API:

| API | 内容 |
| --- | --- |
| `GET /api/cards/{id}/extractions` | 名刺の解析履歴 |
| `GET /api/cards/{id}/corrections` | 名刺の補正履歴 |
| `GET /api/corrections` | 自分の補正履歴 |
| `PATCH /api/corrections/{id}` | `{"active": false}` で参考から除外、`true` で再有効化 |

補正の記録・参照条件は [名刺処理と補正履歴](processing.md#補正履歴と読み取りへの反映) に記載しています。

## 一覧・検索と更新の互換性

人物一覧のページ取得、カーソル、更新世代、従来の全件取得との互換性は
[人物一覧の取得](contact-list.md#api) を参照してください。

名刺の `revision` は整数、各人物の `revision` は所属名刺の変更を表す文字列です。
同じ秒の変更でも更新されます。名刺検索はすべての検索語を満たす名刺を返し、人物検索は
同じ人物の複数の名刺にまたがってすべての語を満たす場合も返します。

画像変更・削除・再処理は名刺単位で排他制御し、処理中の競合操作は409を返します。
手動項目の保存は処理中も可能です。
すべての名刺はbzCardローカルユーザーに紐付きます。別ユーザーの名刺は、一覧、
検索、詳細、画像、更新、削除、ジョブ取得のいずれからも取得できません。
