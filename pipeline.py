import anthropic
import requests
import csv
import os
import time
import base64
import re
import markdown as md_converter
from bs4 import BeautifulSoup
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SERP_API_KEY = os.environ.get("SERP_API_KEY")
WP_URL = os.environ.get("WP_URL", "").rstrip("/")
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

OUTPUT_DIR = Path("articles")
OUTPUT_DIR.mkdir(exist_ok=True)

CATEGORY_MAP = {
    "学習方法": "programming", "スクール": "programming",
    "言語・技術": "programming", "IT資格": "programming",
    "学習リソース": "programming", "インフラ": "programming",
    "開発手法": "programming", "開発ツール": "programming",
    "IT転職": "it-career", "キャリア": "it-career", "副業×IT": "it-career",
    "Python・自動化": "dev-automation", "自動化ツール": "dev-automation",
    "投資自動化": "dev-automation", "開発・副業": "dev-automation",
    "AIツール": "ai-tools", "AI技術": "ai-tools", "AI画像": "ai-tools",
    "副業全般": "side-income", "コンテンツ副業": "side-income",
    "デジタル販売": "side-income", "SEO": "side-income",
    "動画": "side-income", "デザイン": "side-income",
    "語学副業": "side-income", "教育副業": "side-income", "生産性": "side-income",
    "投資入門": "investment", "投資信託": "investment",
    "NISA・iDeCo": "investment", "株式投資": "investment",
    "証券口座": "investment", "FX": "investment", "仮想通貨": "investment",
    "FIRE": "investment", "資産形成戦略": "investment",
    "ITエンジニア×投資": "investment", "その他投資": "investment",
    "年金": "investment", "投資学習": "investment",
    "クレカ・ポイント": "money-basics", "節税・節約": "money-basics",
    "家計管理": "money-basics", "税務": "money-basics", "税金・法務": "money-basics",
}

# 意図別プロンプト設定
INTENT_CONFIG = {
    "comparison": {
        "label": "比較・おすすめ系",
        "structure": "導入（250字）→ 比較表（H2）→ 各サービス詳細（H2×3）→ 選び方まとめ（H2）→ まとめ（200字）",
        "extras": "- サービスや選択肢を比較する表を1つ以上入れること\n- キーワードを必ずタイトルの先頭または前半に配置すること",
        "faq_count": 5,
    },
    "howto": {
        "label": "やり方・手順系",
        "structure": "導入（250字）→ 事前準備（H2）→ ステップ1〜3（H2×3、番号リスト形式）→ よくある失敗（H2）→ まとめ（200字）",
        "extras": "- 手順は番号付きリストで具体的に書くこと\n- キーワードを必ずタイトルの先頭または前半に配置すること",
        "faq_count": 3,
    },
    "review": {
        "label": "評判・レビュー系",
        "structure": "導入（250字）→ 基本情報（H2）→ メリット（H2）→ デメリット・注意点（H2）→ こんな人におすすめ（H2）→ まとめ（200字）",
        "extras": "- メリット・デメリットを公平に書くこと\n- キーワードを必ずタイトルの先頭または前半に配置すること",
        "faq_count": 3,
    },
    "concept": {
        "label": "概念・解説系",
        "structure": "導入（250字）→ 基本概念（H2）→ 具体的な仕組み（H2）→ 実践への活かし方（H2）→ まとめ（200字）",
        "extras": "- 専門用語は必ず平易な言葉で説明すること\n- キーワードを必ずタイトルの前半に配置すること",
        "faq_count": 0,
    },
    "general": {
        "label": "一般・情報系",
        "structure": "導入（250字）→ H2見出し3〜4個 → まとめ（200字）",
        "extras": "- キーワードを必ずタイトルの先頭または前半に配置すること",
        "faq_count": 3,
    },
}

_category_id_cache = {}


def get_wp_headers() -> dict:
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def get_category_id(slug: str) -> int | None:
    if slug in _category_id_cache:
        return _category_id_cache[slug]
    res = requests.get(
        f"{WP_URL}/wp-json/wp/v2/categories",
        params={"slug": slug},
        headers=get_wp_headers(),
    )
    data = res.json()
    if data:
        _category_id_cache[slug] = data[0]["id"]
        return data[0]["id"]
    return None


def get_or_create_tag(name: str) -> int | None:
    res = requests.get(
        f"{WP_URL}/wp-json/wp/v2/tags",
        params={"search": name},
        headers=get_wp_headers(),
    )
    for tag in res.json():
        if tag["name"] == name:
            return tag["id"]
    res = requests.post(
        f"{WP_URL}/wp-json/wp/v2/tags",
        json={"name": name},
        headers=get_wp_headers(),
    )
    if res.status_code == 201:
        return res.json()["id"]
    return None


def fetch_article_body(url: str, max_chars: int = 2000) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        res = requests.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(res.text, "lxml")
        for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body
        if not main:
            return ""
        text = re.sub(r'\n{3,}', '\n\n', main.get_text(separator="\n"))
        return text.strip()[:max_chars]
    except Exception:
        return ""


def search_articles(keyword: str, num_results: int = 5) -> list[dict]:
    params = {
        "q": keyword,
        "api_key": SERP_API_KEY,
        "engine": "google",
        "hl": "ja",
        "gl": "jp",
        "num": num_results,
        "tbs": "qdr:y",
    }
    res = requests.get("https://serpapi.com/search", params=params)
    results = []
    for r in res.json().get("organic_results", [])[:num_results]:
        url = r.get("link", "")
        results.append({
            "title": r.get("title", ""),
            "url": url,
            "snippet": r.get("snippet", ""),
            "body": fetch_article_body(url),
        })
    return results


def classify_intent(keyword: str) -> str:
    """キーワードの検索意図をHaikuで分類する"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": (
                f"以下のキーワードの検索意図を1単語で答えてください。\n"
                f"選択肢: comparison / howto / review / concept / general\n\n"
                f"comparison: 比較・おすすめ・ランキング・選び方\n"
                f"howto: 方法・やり方・始め方・作り方・手順\n"
                f"review: 評判・口コミ・レビュー・メリット・デメリット\n"
                f"concept: とは・意味・仕組み・違い・基礎\n"
                f"general: その他\n\n"
                f"キーワード: {keyword}\n"
                f"回答（1単語のみ）:"
            ),
        }],
    )
    intent = message.content[0].text.strip().lower()
    return intent if intent in INTENT_CONFIG else "general"


def build_faq_section(faq_count: int) -> str:
    if faq_count == 0:
        return ""
    return (
        f"\n## よくある質問\n\n"
        f"読者がよく疑問に思う質問と回答を{faq_count}つ、"
        f"以下の形式で書いてください：\n\n"
        f"### Q. 質問文\n回答文（100字程度）\n"
    )


def generate_article(keyword: str, search_results: list[dict], intent: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    config = INTENT_CONFIG[intent]

    search_context = "\n\n".join([
        f"タイトル: {r['title']}\nURL: {r['url']}\n概要: {r['snippet']}"
        + (f"\n本文抜粋:\n{r['body']}" if r.get("body") else "")
        for r in search_results
    ])

    faq_instruction = build_faq_section(config["faq_count"])

    prompt = f"""あなたはITと資産形成に詳しいブロガーです。
ITコンサルタントとして働きながら、Python・AIツール・副業・投資を実践している20代という設定です。

以下のキーワードについて、SEOに強いオリジナルのブログ記事をマークダウン形式で書いてください。

【キーワード】
{keyword}

【記事タイプ】{config["label"]}

【検索上位記事の参考情報】
{search_context}

【記事の条件】
- 文字数：2,500〜3,500字
- 構成：{config["structure"]}
- 読者：ITに興味がある20〜30代の社会人
- トーン：わかりやすく・実践的・一人称は「私」
- 参考情報はあくまで構成の参考にし、文章はオリジナルで書くこと
- 自分が実際に体験・利用したという表現は避け、「知人のエンジニアが」「口コミで」「調べた結果」などの表現を使うこと
- ブログ名や筆者名などの自己紹介文は書かないこと
- タイトルや本文に年号（「2024年版」等）は入れないこと。時期を示す場合は「最新」「現在」を使うこと
{config["extras"]}
{faq_instruction}

【出力形式】※必ずこの形式で出力してください
<!-- slug: 記事の内容を表す英語スラッグ（ハイフン区切り・20文字以内） -->
<!-- meta: メタディスクリプション（120字以内の日本語） -->
<!-- tags: 記事に関連する日本語タグをカンマ区切りで5〜8個 -->

# 記事タイトル（H1）

本文..."""

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    content = message.content[0].text

    slug_match = re.search(r'<!-- slug: (.+?) -->', content)
    slug = slug_match.group(1).strip() if slug_match else f"post-{int(time.time())}"

    title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else keyword

    tags_match = re.search(r'<!-- tags: (.+?) -->', content)
    tags = [t.strip() for t in tags_match.group(1).split(",")] if tags_match else []

    meta_match = re.search(r'<!-- meta: (.+?) -->', content)
    meta = meta_match.group(1).strip() if meta_match else ""

    body = re.sub(r'^<!--.*?-->\s*', '', content, flags=re.MULTILINE)
    body = re.sub(r'^# .+\n?', '', body, count=1, flags=re.MULTILINE)
    html_content = md_converter.markdown(body, extensions=["extra", "nl2br"])

    disclaimer = (
        '<div class="disclaimer" style="margin-top:2em;padding:1em;'
        'background:#f5f5f5;border-left:4px solid #ccc;font-size:.9em;color:#555;">'
        '※本記事の情報は執筆時点のものです。料金・サービス内容・制度は変更される場合があります。'
        '最新情報は各公式サイトにてご確認ください。'
        '</div>'
    )
    html_content += disclaimer

    return {"title": title, "slug": slug, "content": html_content, "tags": tags, "meta": meta}


def post_to_wordpress(
    title: str,
    content: str,
    slug: str,
    category_slug: str,
    tags: list[str],
    meta: str = "",
) -> dict | None:
    category_id = get_category_id(category_slug)
    tag_ids = [tid for t in tags[:8] if (tid := get_or_create_tag(t))]

    payload = {
        "title": title,
        "content": content,
        "slug": slug,
        "status": "draft",
        "categories": [category_id] if category_id else [],
        "tags": tag_ids,
        "meta": {"_yoast_wpseo_metadesc": meta} if meta else {},
    }

    res = requests.post(
        f"{WP_URL}/wp-json/wp/v2/posts",
        json=payload,
        headers=get_wp_headers(),
    )

    if res.status_code == 201:
        return res.json()
    print(f"  → WordPress投稿エラー: {res.status_code} {res.text[:200]}")
    return None


def save_article(keyword: str, content: str) -> Path:
    safe = keyword.replace(" ", "_").replace("/", "_")
    filepath = OUTPUT_DIR / f"{safe}.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def load_keywords(csv_path: str = "keywords.csv") -> list[dict]:
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def process_keywords(start: int = 0, end: int = 10, post_to_wp: bool = True):
    keywords = load_keywords()
    target = keywords[start:end]

    print(f"\n=== 記事生成開始 {start+1}〜{start+len(target)}件目 ===\n")

    for i, row in enumerate(target, start=start + 1):
        keyword = row["キーワード"]
        category_slug = CATEGORY_MAP.get(row["カテゴリ"], "programming")

        print(f"[{i}/{len(keywords)}] {keyword}")

        safe = keyword.replace(" ", "_").replace("/", "_")
        if (OUTPUT_DIR / f"{safe}.md").exists():
            print(f"  → スキップ（生成済み）\n")
            continue

        try:
            print(f"  → 意図分類中...")
            intent = classify_intent(keyword)
            print(f"  → 意図: {intent} ({INTENT_CONFIG[intent]['label']})")

            print(f"  → 検索中...")
            search_results = search_articles(keyword)
            if not search_results:
                print(f"  → 検索結果なし・スキップ\n")
                continue

            print(f"  → 記事生成中...")
            article = generate_article(keyword, search_results, intent)

            saved_path = save_article(keyword, article["content"])
            print(f"  → ローカル保存: {saved_path}")

            if post_to_wp:
                print(f"  → WordPress下書き投稿中...")
                result = post_to_wordpress(
                    title=article["title"],
                    content=article["content"],
                    slug=article["slug"],
                    category_slug=category_slug,
                    tags=article["tags"],
                    meta=article["meta"],
                )
                if result:
                    print(f"  → 投稿完了: {WP_URL}/?p={result['id']} (下書き)\n")
                else:
                    print(f"  → 投稿失敗（ローカルには保存済み）\n")
            else:
                print(f"  → ローカル保存のみ\n")

            time.sleep(2)

        except Exception as e:
            print(f"  → エラー: {e}\n")
            continue

    print("=== 完了 ===")


if __name__ == "__main__":
    process_keywords(start=0, end=10)
