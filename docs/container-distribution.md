# Dockerイメージでの配布

UI・API・kanaをGitHub Container Registry（GHCR）へ配布する。
`docker-compose.yml` には `build` 指定がなく、利用者側でソースをビルドする必要はない。
ソースビルド用の構成は `docker-compose.develop.yml` に分けている。
初回の公開は、以下のワークフローをGitHubへ反映してバージョンタグをpushした後に行われる。
対応プラットフォームは検証済みの `linux/amd64`。

| イメージ | 内容 |
| --- | --- |
| `ghcr.io/iz69/bzcard-ui` | Reactアプリ、nginx、起動時の設定生成 |
| `ghcr.io/iz69/bzcard-api` | FastAPI、OCR依存、名刺処理 |
| `ghcr.io/iz69/bzcard-kana` | 氏名読み推論のコード、PyTorch |

Ollamaは `ollama/ollama` を使う。kana・Ollamaのモデル重みは、READMEの公式配布先から
別途取得する。OCRモデルは初回利用時に取得され、データ保存先の
`cache/huggingface` に永続化する。APIイメージ側でキャッシュの保存先を
`/data/cache/huggingface` に固定しているため、配布用・開発用とも同じ場所を使う。

## 公開後の導入

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

[READMEの起動手順](../README.md#ソースからの起動手順) にあるOllamaのモデル登録と、
kana重みの取得・チェックサム確認を実施する。これらのモデル準備にはソースビルドは不要。
データ保存先を変更した場合は、kana重みもその保存先の
`models/kanjikana-1.9o/checkpoint_best.pt` に配置する。
重みがない場合、kanaコンテナの起動はエラーになる。

```sh
docker compose up -d
```

ホスト側のリバースプロキシを設定し、WebUIの初回画面で管理者を作成する。
`docker-compose.yml` が既定で選ばれるため、`-f` の指定は不要。

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

## メンテナーの公開手順

[container-images.yml](../.github/workflows/container-images.yml) がPRとmainへのpushで検証を行う。
`v0.1.0` のようなバージョンタグのpushでは、すべての検証が成功した後で
変更のあるイメージだけをGHCRへpushする。GitHub Actionsの `GITHUB_TOKEN` に `packages: write` を付与しているため、
個人アクセストークンの登録は不要。

```sh
# 検証済みのコミットをGitHubへ反映した後に実行
git tag v0.1.0
git push origin v0.1.0
```

`v0.1.0` はイメージの `0.1.0` タグになる。安定版には `latest`、
コミット識別用には `sha-...` も付く。プレリリースは `latest` を更新しない。

公開対象は [image_release.py](../.github/scripts/image_release.py) が、イメージごとの
公開済み `latest` に記録されたソースコミットと、今回のリリースコミットを比較して選ぶ。
直前のGitタグを比較元にしないため、公開に失敗したリリースの変更も次回の対象に残る。
初回公開や、比較用のソース情報がないイメージは公開対象になる。
読み取りが通信・認証エラーで失敗した場合は、公開対象の判定を中止する。

| イメージ | 変更を判定するファイル |
| --- | --- |
| API | `api/src/`、`api/requirements.txt`、`api/Dockerfile`、`api/.dockerignore` |
| UI | `ui/` 内のアプリ・設定・アセット。テスト、`node_modules`、`dist`、`.env*` は除外 |
| kana | `api/kana/`、`api/Dockerfile.kana`、`api/.dockerignore` |

ドキュメント・テスト・公開ワークフローだけの変更では、イメージの新しいバージョンを公開しない。
変更のないイメージには新しいバージョンタグを作らず、その `latest` も更新しない。
判定結果はGitHub Actionsの「Image release plan」に、イメージごとの公開前後のバージョンとして表示する。
全サービスを検証してから、公開対象のイメージをビルド・pushする。
リリース同士の同時公開を防ぐため、同じconcurrency groupで実行する。

例えばAPI/UIだけを変更した `v0.9.2`、その後APIだけを変更した `v0.9.3` を公開すると、
各イメージの最新安定版は次のようになる。

| イメージ | `v0.9.2` 公開後 | `v0.9.3` 公開後 |
| --- | --- | --- |
| API | `0.9.2` | `0.9.3` |
| UI | `0.9.2` | `0.9.2` |
| kana | `0.9.1` | `0.9.1` |

公開を再実行した場合、既に同じソースで公開済みのイメージは通常の変更判定で除外する。
プレリリースも安定版の `latest` と比較し、公開済みの同じプレリリースは再公開しない。
安定版に昇格する際は、安定版との差分があるイメージを公開する。
公開済みのバージョンを別のコミットで上書きしようとすると失敗する。

ベースイメージの更新など、ソースを変えずに再ビルドしたい場合は、新しいバージョンタグを用意して
`workflow_dispatch` の `force_images` に `api`・`ui`・`kana`・`api,ui`・`all` を指定する。
指定したイメージを通常の変更判定の対象に追加する。GitHub CLIを使う場合は次のように実行する。

```sh
# 新しいタグをpushした後、そのタグを指定して追加の再ビルドを行う例
gh workflow run container-images.yml --ref v0.9.4 -f force_images=api
```

既に公開済みの同じバージョンは再ビルドしないため、必ず新しいバージョンを使う。
mainへのpushやmainを対象にした手動実行では検証だけを行い、イメージは公開しない。

WebUIには公開時のGitタグをビルド時に埋め込み、タイトル右側に表示する。
`latest` で起動しても実際のリリースタグを確認できる。
ローカルビルドでは既定で `dev` を表示し、独自の表示を指定する場合は
ビルド引数 `BZCARD_BUILD_VERSION` を渡す。
初回公開後は、GitHubのPackages設定で3パッケージのVisibilityを `Public` に変更する。
これで利用者はGHCRにログインせずpullできる。

GHCRの公開方法とVisibilityについては
[GitHub公式ドキュメント](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) を参照。

## 実装と検証

UIはViteの相対パスで一度だけビルドする。起動時に公開パスを検証し、
生成したHTMLの `base` と `runtime-config.js` を配置する。コンパイル済みJS/CSSは変更しない。
HTMLと設定JSは `no-store`、ハッシュ付きJS/CSSは長期キャッシュで配信する。
nginxは正規表現のrewriteへサブパスを埋め込まず、ファイルのaliasで配信する。

`npm run test:container` は同じイメージで以下を検証する。

- `/bzcard/`、`/`、`/tools/liff/`、`/liff/`、`/v1.0+cards/`、`/名刺 管理/`
- HTML・設定JS・JS/CSS・アイコン・LIFFページの取得とキャッシュ指定
- 末尾スラッシュの補完、存在しないアセットの404、再起動後の設定
- ログイン移行、ユーザー切替、配置ごとのセッション、ルートAPI、LIFFの操作
- 人物一覧の50件ずつの取得、全件検索、スクロール、取得失敗時の再試行と編集の保持
- 配置ごとにJS/CSSの内容とDockerイメージIDが同一であること

ブラウザでのAPI応答は架空のデータを使う。
APIの公開パスと既存機能は、別途APIの回帰テストで検証する。
