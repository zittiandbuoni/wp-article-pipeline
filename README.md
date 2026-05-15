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

### keywords.csv の形式

```csv
番号,キーワード,軸,カテゴリ
1,プログラミング 独学 方法,A,学習方法
2,副業 おすすめ 在宅,B,副業全般
```

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
