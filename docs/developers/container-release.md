# Dockerイメージの公開と検証

イメージを公開するメンテナーと、配信処理を変更する開発者向けの文書です。
公開済みのイメージの導入・更新は [管理者向けガイド](../administrators/README.md) を参照してください。
配布用は [docker-compose.yml](../../docker-compose.yml)、ソースビルド用は
[docker-compose.develop.yml](../../docker-compose.develop.yml) を使います。

## メンテナーの公開手順

[container-images.yml](../../.github/workflows/container-images.yml) がPRとmainへのpushで検証を行う。
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

公開対象は [image_release.py](../../.github/scripts/image_release.py) が、イメージごとの
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

WebUIとAPIには、それぞれ公開時のGitタグをビルド引数 `BZCARD_BUILD_VERSION` で渡す。
UIは `VITE_BUILD_VERSION` としてコンパイル済みJavaScriptへ埋め込み、APIはイメージ内の
環境変数 `BZCARD_BUILD_VERSION` に保存する。バージョン専用ファイルの更新や、実行時のGitHubへの問い合わせは行わない。
PC版のタイトル右側には `WebUI v0.9.2 / API v0.9.3` のように両方を表示する。
API側は認証付きの `GET /api/system/versions` の `api.version` から取得する。
イメージごとに変更のあるときだけ公開するため、UIとAPIのバージョンは一致するとは限らない。
`latest` で起動しても実際のリリースタグを確認できる。
ローカルビルドでは `WebUI dev / API dev` と表示する。
独自のバージョンを指定する場合は各イメージのビルド引数 `BZCARD_BUILD_VERSION` を渡す。
旧APIイメージなどで `api.version` が取得できない場合は `API 不明` と表示する。
初回公開後は、GitHubのPackages設定で3パッケージのVisibilityを `Public` に変更する。
これで利用者はGHCRにログインせずpullできる。

GHCRの公開方法とVisibilityについては
[GitHub公式ドキュメント](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) を参照。

## 実装と検証

### 稼働中のWebUIをローカルdev版へ切り替える

印刷機能などUIだけを検証する場合は、ローカルソースから `bzcard-ui:dev` を作り、
[docker-compose.ui-dev.yml](../../docker-compose.ui-dev.yml) を稼働構成に重ねてWebUIを更新できます。
ビルド時の表示バージョンは `dev` です。

```sh
docker build -t bzcard-ui:dev ./ui
docker compose -f docker-compose.yml -f docker-compose.ui-dev.yml up -d --no-deps ui
```

別のディレクトリから稼働している構成では、最初の `-f` に稼働中のcomposeファイルを指定し、
2つ目にこのリポジトリの `docker-compose.ui-dev.yml` の絶対パスを指定します。
稼働中と同じプロジェクト名・環境設定で実行してください。
配布版へ戻す場合は、稼働中のcomposeファイルだけで `up -d --no-deps ui` を実行します。

APIを修正した場合は `docker build -t bzcard-api:dev ./api` でビルドし、
[docker-compose.api-dev.yml](../../docker-compose.api-dev.yml) を重ねて同じ方法で `api` を更新します。
WebUIもdev版で稼働している場合は、UIとAPIの両方のoverrideファイルを指定します。
更新前のバックアップは [設定・運用ガイド](../administrators/README.md#バックアップとdb移行) を参照してください。

### UIの検証

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

`npm run test:print` はPCの印刷ボタン、表面だけの画像掲載と元画像へのフォールバック、
未保存編集のスナップショット、印刷取消と後片付け、長文の折り返し・高さ調整、
取得失敗時の再試行、名刺切替時の準備中断を架空データで検証します。
ブラウザの印刷ダイアログはテスト内で置き換え、実際の帳票DOMと画像を検証します。
`BZCARD_SCREENSHOT_DIR` を指定すると画面・帳票のPNGとA4のPDFも出力します。
