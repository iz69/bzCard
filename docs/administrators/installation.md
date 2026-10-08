# Dockerイメージでの導入

サーバーを用意する管理者向けの手順です。導入後の設定・更新・バックアップは
[設定・運用ガイド](README.md)、名刺の操作は [利用ガイド](../users/README.md) を参照してください。

公開済みのUI・API・kanaをGitHub Container Registry（GHCR）から取得して導入します。
[配布用Compose](../../docker-compose.yml) には `build` 指定がなく、配置先でソースをビルドする必要はありません。
ソースを変更する場合は [開発ガイド](../developers/README.md) を参照してください。
対応プラットフォームは検証済みの `linux/amd64`。

| イメージ | 内容 |
| --- | --- |
| `ghcr.io/iz69/bzcard-ui` | Reactアプリ、nginx、起動時の設定生成 |
| `ghcr.io/iz69/bzcard-api` | FastAPI、OCR依存、名刺処理 |
| `ghcr.io/iz69/bzcard-kana` | 氏名読み推論のコード、PyTorch |

Ollamaは `ollama/ollama` を使う。kana・Ollamaのモデル重みは、このガイドの「モデルの準備」で示す公式配布先から
別途取得する。OCRモデルは初回利用時に取得され、データ保存先の
`cache/huggingface` に永続化する。APIイメージ側でキャッシュの保存先を
`/data/cache/huggingface` に固定しているため、配布用・開発用とも同じ場所を使う。

## 配布ファイルの準備

DockerとDocker Composeを用意し、公開バージョンの `docker-compose.yml` と
`.env.example` を同じディレクトリへ置く。リポジトリ全体やGitは不要。
以下の `0.9.1` は取得するComposeファイルの公開バージョンに置き換える。

```sh
mkdir bzcard
cd bzcard
BZCARD_RELEASE_VERSION=0.9.1
curl -fL "https://raw.githubusercontent.com/iz69/bzCard/v${BZCARD_RELEASE_VERSION}/docker-compose.yml" -o docker-compose.yml
curl -fL "https://raw.githubusercontent.com/iz69/bzCard/v${BZCARD_RELEASE_VERSION}/.env.example" -o .env.example
cp .env.example .env
```

`.env` に公開パスとデータ保存先を設定する。

```env
UI_BASE_PATH=/bzcard/
API_BASE_PATH=/bzcard-api
BZCARD_DATA_DIR=./data
BZCARD_OLLAMA_DIR=./ollama
```

イメージの配布先・タグとホスト側ポートは `docker-compose.yml` に直接記載する。
既定ではUI・API・kanaそれぞれの `latest` を使用し、WebUIは15174番、APIは18081番で公開する。
バージョンを固定する場合は、対象サービスの `image` を
`ghcr.io/iz69/bzcard-api:0.9.1` のように変更する。サービスごとに異なるバージョンも指定できる。
以降の公開では変更のあるイメージだけが更新されるため、3つの最新バージョンは一致しない場合がある。
`latest` を使う配置では、それぞれのイメージの最新安定版を取得する。

コンテナ名は開発用と同じ `bzcard-api`・`bzcard-ui`・`bzcard-kana`・`bzcard-ollama` に固定する。
同じホストに複数配置する場合は、`container_name` とホスト側ポートを配置ごとに変更する。

標準のComposeファイルでイメージを取得し、モデルの準備をする。

```sh
unset COMPOSE_FILE
docker compose pull
docker compose up -d ollama
```

## モデルの準備

この節は配布用・ソースビルド用の両方で使います。選択したCompose構成の
Ollamaを起動した状態で、配置先のディレクトリから実行してください。

### Ollamaのモデル登録

`LLM_PROVIDER=ollama` の場合、初回はOllamaモデルを取得してください。既定の
`bzcard-lfm-jp:202606` は、公式GGUFから作成するローカルモデルです。

```sh
curl -fL https://huggingface.co/LiquidAI/LFM2.5-1.2B-JP-202606-GGUF/resolve/main/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf -o /tmp/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf
docker compose exec ollama mkdir -p /root/.ollama/import
docker compose cp /tmp/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf ollama:/root/.ollama/import/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf
docker compose exec ollama sh -lc 'printf "FROM /root/.ollama/import/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf\\nPARAMETER num_ctx 4096\\n" > /root/.ollama/import/Modelfile.lfm-jp'
docker compose exec ollama ollama create bzcard-lfm-jp:202606 -f /root/.ollama/import/Modelfile.lfm-jp
rm /tmp/LFM2.5-1.2B-JP-202606-Q4_K_M.gguf
```

### 氏名ふりがなモデル

氏名ふりがなモデルの重みを公式配布先から取得します。約861 MiBです。モデルのコードはMITライセンスですが、別配布の重みの利用条件は公式サイトで確認してください。

```sh
BZCARD_MODEL_DATA_DIR=./data
mkdir -p "$BZCARD_MODEL_DATA_DIR/models/kanjikana-1.9o"
curl -fL https://kktg.digital.go.jp/public/core/1.9o/ai/checkpoint_best.pt -o "$BZCARD_MODEL_DATA_DIR/models/kanjikana-1.9o/checkpoint_best.pt"
echo "cd9d29ad7ecf33afb02a14b807716fbf07fc6d535b9fa385bc431cdf7e53850f  $BZCARD_MODEL_DATA_DIR/models/kanjikana-1.9o/checkpoint_best.pt" | sha256sum -c -
```

上の `BZCARD_MODEL_DATA_DIR` はダウンロード先を指定するシェル変数です。
配布用Composeで `BZCARD_DATA_DIR` を変更した場合は同じ保存先を指定してください。
ソースビルド用Composeのモデル保存先は `./data` です。
重みは、その保存先の `models/kanjikana-1.9o/checkpoint_best.pt` に配置します。
重みがない場合、kanaコンテナの起動はエラーになります。

## 初回起動

```sh
docker compose up -d
```

ホスト側の [リバースプロキシ](README.md#nginx設定例) を設定し、WebUIを開きます。
標準のローカル確認用URLは `http://localhost:15174/bzcard/` です。
プロキシなしの場合はログイン画面のAPI URLに `http://localhost:18081` を指定してください。

初回画面で [管理者のログインIDとパスワード](README.md#初回の管理者作成) を作成します。
以後の操作は [利用ガイド](../users/README.md) を参照してください。
`docker-compose.yml` が既定で選ばれるため、`-f` の指定は不要です。

## 任意のサブパス

例えば、次の設定に変更すると同じイメージをそのまま使える。

```env
UI_BASE_PATH=/tools/cards/
API_BASE_PATH=/tools/cards-api
```

```nginx
location /tools/cards-api/ {
    client_max_body_size 60m;
    proxy_pass http://127.0.0.1:18081/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location /tools/cards/ {
    proxy_pass http://127.0.0.1:15174;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

UIは公開プレフィックスを保持して転送し、APIは除いて転送する。
UIは従来のプレフィックスを除く転送にも対応するが、任意のサブパスでは保持する設定を推奨する。
`/liff/` など、除いたパスと公開パスが重なる設定でも意図したルートを選べる。

`UI_BASE_PATH=/` も使用できる。APIも `API_BASE_PATH=/` にする場合は、
`/api/`・`/line/`・`/docs`・`/redoc`・`/openapi.json`・`/ping` をAPIへ、
残りをUIへ転送する。UIコンテナ自体はAPI通信を中継しない。
プロキシなしの直接確認では、ログイン画面のAPI URLに `http://localhost:18081` を指定する。

UIコンテナだけを利用する場合、`API_BASE_PATH` には `https://api.example/cards-api` のような
別オリジンのHTTP(S) URLも指定できる。APIの `BASE_PATH` はURLのパス部分を指定する設定であり、
配布用Composeで共通の `API_BASE_PATH` を使う場合は絶対パスを指定する。

設定変更後はコンテナを再作成する。UIイメージの再ビルドは不要。

```sh
docker compose up -d
```

LINEのWebhook URLとLIFF URLも新しい公開パスに合わせる。
LIFFページは `<UI_BASE_PATH>liff`。UIの公開パスや初期API接続先を変更すると、
ログイン情報の保存先が変わるため再ログインする。
