# bzCard

bzCard は、日本語名刺向けのセルフホスト型名刺管理DBです。

JPEG/PNGの名刺画像を受け取り、元画像と補正後画像を保存し、OCRで文字を読み取り、
Ollama上のローカルLLMで氏名・会社名・住所・電話番号などの項目抽出を行います。
WebUIとLINE公式アカウント連携に対応しています。

Androidネイティブアプリは開発済みですが、実際の使用感を試しつつ調整中です。

各利用者はローカルID・パスワードでログインし、自身のLINE公式アカウントを設定できます。
公開環境に置く場合は、必ずHTTPSとリバースプロキシ側のアクセス制限を併用してください。

左一覧・右詳細（表示データはサンプルです）
<img width="1653" height="863" alt="image" src="https://github.com/user-attachments/assets/b71c509b-52a5-4024-bbc8-a77fb17f0e6a" />

AIによるOCRとデータ抽出結果（表示データはサンプルです）
<img width="929" height="468" alt="image" src="https://github.com/user-attachments/assets/f5658148-93e1-4b8c-a3a8-a189bcbb7647" />


## 主な機能

- WebUIからの名刺画像アップロード
- LINE公式アカウントのWebhookからの画像登録
- 元画像、補正後画像、サムネイルの保存
- 自動切り抜き、台形補正、明るさ・コントラスト・シャープネス補正
- LINEやスマホ撮影で画像が90度回転して届いた場合の自動補正
- OCRによる文字の読み取り
- Ollama上のローカルLLMによる項目抽出
- デジタル庁の氏名漢字カナ突合モデルによる、印刷された読みがない氏名のふりがな推論
- 表面・裏面画像の管理
- 画像ハッシュによる簡易重複検出
- ログインID・パスワードによるユーザー認証
- ユーザーに紐付けたLINE公式アカウントからの名刺登録
- SQLite保存
- 人物一覧の初回50件表示とスクロールによる追加取得、過去の名刺も対象にした検索
- 名刺ではなさそうな画像を `not_card` として停止

## ドキュメント

| 対象 | 読むもの |
| --- | --- |
| 名刺を登録・検索する利用者 | [利用ガイド](docs/users/README.md) |
| サーバーを導入する管理者 | [Dockerイメージでの導入](docs/administrators/installation.md) |
| サーバーを設定・更新する管理者 | [設定・運用ガイド](docs/administrators/README.md) |
| ソースを変更・検証する開発者 | [開発ガイド](docs/developers/README.md) |

全体の案内は [docs の目次](docs/README.md) を参照してください。
導入には公開済みのDockerイメージを利用でき、配置先でのソースビルドは不要です。

## 補足

- 画像から文字を読む精度は主にOCRエンジンと画像品質に依存します。
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
