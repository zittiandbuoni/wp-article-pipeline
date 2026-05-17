# wp-article-pipeline

キーワードリストからSEO記事を自動生成してWordPressに下書き投稿するPythonパイプラインです。

## 概要

### 記事生成パイプライン（`pipeline.py`）

```
keywords.csv（キーワード一覧）
  ↓
① SerpAPI でGoogle検索 → 上位記事の本文をクロール
  ↓
② Claude API で記事生成（2,500〜3,500字・HTML変換・免責事項付き）
  ↓
③ ローカルにMarkdown保存 + WordPress REST APIで下書き投稿
```

### リライト・アイキャッチパイプライン（`factcheck_rewrite.py`）

```
WordPress 下書きID を指定
  ↓
① Claude Haiku でファクトチェック（事実誤り・個人体験・誇大表現・年号古い等を検出）
  ↓
② Claude Sonnet でリライト（指摘反映 + 個人体験削除 + 断定緩和 + SEO構成維持）
  ↓
③ Imagen 4 でアイキャッチ画像生成（16:9）
  ↓
④ WordPress メディアライブラリへアップロード → featured_media に設定 → 下書き更新
```

### キーワード優先度スコアリング（`prioritize.py`）

```
keywords.csv（未処理キーワード）
  ↓
Claude Haiku でアフィリエイト収益ポテンシャルをバッチスコアリング
  ↓
上位N件に絞り込んだ keywords.csv を出力
```

## 必要なもの

- [Anthropic API キー](https://console.anthropic.com/)
- [SerpAPI キー](https://serpapi.com/)
- [Google AI Studio API キー](https://aistudio.google.com/)（アイキャッチ生成用・有料プラン必須）
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

### .env の設定項目

```
ANTHROPIC_API_KEY=sk-ant-...
SERP_API_KEY=...
GOOGLE_API_KEY=...           # Imagen 4 使用時に必要（有料プラン）
WP_URL=https://your-site.com
WP_USER=your_wp_username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

### WordPressの事前設定

XserverなどApache環境では、`.htaccess` の先頭に以下を追加してください（Authorizationヘッダーの通過に必要）。

```apache
SetEnvIf Authorization .+ HTTP_AUTHORIZATION=$0
```

## 使い方

### 1. 記事生成（`pipeline.py`）

#### keywords.csv の作り方

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

#### 実行

```python
from pipeline import process_keywords

# キーワード1〜50件目をWordPress下書き投稿まで実行
process_keywords(start=0, end=50, post_to_wp=True)

# ローカル保存のみ（WP投稿しない）
process_keywords(start=0, end=50, post_to_wp=False)
```

生成済みキーワードは自動でスキップされます。

### 2. リライト＋アイキャッチ生成（`factcheck_rewrite.py`）

WordPressの下書き投稿IDを指定して実行します。

```bash
# 通常実行（ファクトチェック→リライト→アイキャッチ生成→WP下書き更新）
python3 factcheck_rewrite.py 123
python3 factcheck_rewrite.py 123 456 789   # 複数指定可

# アイキャッチ生成・設定のみ（リライトはスキップ）
python3 factcheck_rewrite.py 123 --eyecatch-only

# WP更新なし・動作確認用
python3 factcheck_rewrite.py 123 --dry-run

# アイキャッチ生成をスキップ
python3 factcheck_rewrite.py 123 --no-eyecatch
```

#### ファクトチェックの検出項目

| 種別 | 内容 |
|------|------|
| 事実誤り | 技術仕様・業界標準の誤記 |
| 時代遅れ | 廃止・非推奨になったライブラリ・ツール情報 |
| 個人体験 | 「私は〜」「知人の〜」など一人称・伝聞表現 |
| 誇大表現 | 「必ず〜」「〜はずです」など根拠のない断定 |
| 数値未確認 | 出典のない統計・数値 |
| 年号古い | 「2024年調査」など古い年が入った引用 |
| 制度変更リスク | 法律・税制・料金など変更頻度が高い断定 |
| 最上級根拠不足 | 「唯一の〜」「最も〜」など根拠のない最上級表現 |

### 3. キーワード優先度スコアリング（`prioritize.py`）

```bash
python3 prioritize.py
```

未処理キーワードをClaude Haikuでスコアリングし、上位200件に絞り込んで `keywords.csv` を更新します。

## コスト目安

### 記事生成（pipeline.py）

| 項目 | 単価 | 100記事あたり |
|------|------|--------------|
| Claude Sonnet（記事生成） | 約7円/記事 | 約700円 |
| SerpAPI | 約1円/記事 | 約100円 |

### リライト＋アイキャッチ（factcheck_rewrite.py）

| 項目 | 単価 | 10記事あたり |
|------|------|-------------|
| Claude Haiku（ファクトチェック） | 約1円/記事 | 約10円 |
| Claude Sonnet（リライト） | 約5円/記事 | 約50円 |
| Imagen 4（アイキャッチ） | $0.04/枚 | 約60円 |

## ライセンス

MIT
