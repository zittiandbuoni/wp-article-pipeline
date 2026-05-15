# wp-article-pipeline

キーワードリストからSEO記事を自動生成してWordPressに下書き投稿するPythonパイプラインです。

## 概要

```
keywords.csv（キーワード一覧）
  ↓
① SerpAPI でGoogle検索 → 上位記事の本文をクロール
  ↓
② Claude API で記事生成（2,500〜3,500字・HTML変換・免責事項付き）
  ↓
③ ローカルにMarkdown保存 + WordPress REST APIで下書き投稿
```

## 必要なもの

- [Anthropic API キー](https://console.anthropic.com/)
- [SerpAPI キー](https://serpapi.com/)
- WordPressサイト（REST API有効 + アプリケーションパスワード発行済み）

## セットアップ

```bash
git clone https://github.com/your-username/wp-article-pipeline.git
cd wp-article-pipeline

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.sample .env
# .env を編集して各APIキーを設定
```

### WordPressの事前設定

XserverなどApache環境では、`.htaccess` の先頭に以下を追加してください（Authorizationヘッダーの通過に必要）。

```apache
SetEnvIf Authorization .+ HTTP_AUTHORIZATION=$0
```

## 使い方

### keywords.csv の作り方

`keywords.csv` という名前でプロジェクトルートに配置してください（Gitには含まれません）。

```csv
番号,キーワード,軸,カテゴリ
1,プログラミング 独学 方法,A,学習方法
2,副業 おすすめ 在宅,B,副業全般
3,NISA 始め方 初心者,C,NISA・iDeCo
```

| カラム | 説明 |
|--------|------|
| 番号 | 連番（処理順） |
| キーワード | 検索・記事生成に使うキーワード |
| 軸 | テーマ軸（任意ラベル） |
| カテゴリ | WordPressカテゴリのマッピングキー（後述） |

**カテゴリの対応表**（`pipeline.py` の `CATEGORY_MAP` で定義）

| CSVカテゴリ例 | WordPressスラッグ |
|--------------|-----------------|
| 学習方法 / スクール / 言語・技術 | `programming` |
| IT転職 / キャリア | `it-career` |
| Python・自動化 / 自動化ツール | `dev-automation` |
| AIツール / AI技術 | `ai-tools` |
| 副業全般 / SEO / デザイン | `side-income` |
| 投資入門 / NISA・iDeCo / 株式投資 | `investment` |
| クレカ・ポイント / 節税・節約 | `money-basics` |

WordPress側に対応するカテゴリスラッグを事前に作成しておいてください。

### 実行

```python
from pipeline import process_keywords

# キーワード1〜50件目をWordPress下書き投稿まで実行
process_keywords(start=0, end=50, post_to_wp=True)

# ローカル保存のみ（WP投稿しない）
process_keywords(start=0, end=50, post_to_wp=False)
```

生成済みキーワードは自動でスキップされます。

## コスト目安

| 項目 | 単価 | 100記事あたり |
|------|------|--------------|
| Claude API（Sonnet） | 約7円/記事 | 約700円 |
| SerpAPI | 約1円/記事 | 約100円 |

## ライセンス

MIT
