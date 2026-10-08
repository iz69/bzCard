# ドキュメント

作業する人と目的に合わせて、次のガイドを参照してください。
自分のサーバーで使う場合は、管理者向けの導入を終えてから利用ガイドへ進みます。

| 対象 | 目的 | 入口 |
| --- | --- | --- |
| 利用者 | ログイン、名刺の登録・検索・修正、LINE連携 | [利用ガイド](users/README.md) |
| 管理者 | サーバーの導入、公開設定、利用者管理、更新・バックアップ | [設定・運用ガイド](administrators/README.md) |
| 開発者 | ソースビルド、テスト、API・処理の仕様、イメージ公開 | [開発ガイド](developers/README.md) |

## 利用者向け

- [利用ガイド](users/README.md): WebUIの操作と読み取り結果の確認、利用者自身のLINE設定。

## 管理者向け

- [Dockerイメージでの導入](administrators/installation.md): 配布ファイルの取得、モデルの準備、初回起動。
- [設定・運用ガイド](administrators/README.md): 環境変数、公開パス、利用者管理、更新・移行、バックアップ、処理復旧。

## 開発者向け

- [開発ガイド](developers/README.md): 構成、ソースからの起動・更新、開発時チェック。
- [API利用と互換性](developers/api.md): 認証、アップロード、バージョン確認、解析・補正履歴API。
- [名刺処理と補正履歴](developers/processing.md): 処理の流れ、補正の参照、DB移行・ワーカーの仕組み。
- [人物一覧の取得](developers/contact-list.md): DBの要約、ページ取得、検索、検証条件。
- [氏名・フリガナの判断と回帰検証](developers/person-name-reading.md): 氏名と読みの採用規則、解析履歴の再生。
- [Dockerイメージの公開と検証](developers/container-release.md): メンテナーのリリース手順、公開対象の判定、配信の実装。
- [実装チェックリストと検証記録](developers/implementation-checklist.md): 実装項目、過去の検証・本番反映の記録。

検証記録の件数や反映状況は、各文書に記載された日時の結果です。
