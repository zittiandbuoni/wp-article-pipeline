#!/usr/bin/env python3
"""
特定のWordPress下書きをClaudeでファクトチェック→リライト→アイキャッチ生成するスクリプト

使い方:
  python factcheck_rewrite.py 123
  python factcheck_rewrite.py 123 456 789
  python factcheck_rewrite.py 123 --dry-run      # WP更新なし（確認用）
  python factcheck_rewrite.py 123 --no-eyecatch  # アイキャッチ生成をスキップ

処理フロー:
  1. WordPress REST API で下書き取得
  2. Claude (Haiku) でファクトチェック → 問題点をJSON抽出
  3. Claude (Sonnet) でリライト（指摘反映 + 個人体験表現削除 + 品質改善）
  4. Gemini Imagen 3 でアイキャッチ画像生成
  5. WP メディアライブラリへアップロード → featured_media に設定
  6. WordPress 下書きを更新（ステータスは draft のまま）
"""

import sys
import os
import re
import json
import base64
import argparse
import anthropic
import requests
import markdown as md_converter
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_API_KEY    = os.environ.get("GOOGLE_API_KEY")
WP_URL = os.environ.get("WP_URL", "").rstrip("/")
WP_USER = os.environ.get("WP_USER")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

MODEL_FACTCHECK = "claude-haiku-4-5"
MODEL_REWRITE   = "claude-sonnet-4-5"
MODEL_IMAGE     = "imagen-4.0-generate-001"


# ── WordPress helpers ──────────────────────────────────────────────────────────

def get_wp_headers() -> dict:
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def fetch_post(post_id: int) -> dict:
    """WP REST API で投稿を取得（下書き含む）"""
    res = requests.get(
        f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
        headers=get_wp_headers(),
        params={"context": "edit"},
    )
    if res.status_code != 200:
        raise RuntimeError(f"投稿取得失敗 (ID:{post_id}): HTTP {res.status_code} — {res.text[:200]}")
    return res.json()


def update_post(post_id: int, new_html: str, new_title: str = None) -> dict:
    """WP 下書きのコンテンツを更新（ステータスは変更しない）"""
    payload = {"content": new_html}
    if new_title:
        payload["title"] = new_title
    res = requests.post(
        f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
        headers=get_wp_headers(),
        json=payload,
    )
    if res.status_code not in (200, 201):
        raise RuntimeError(f"投稿更新失敗 (ID:{post_id}): HTTP {res.status_code} — {res.text[:200]}")
    return res.json()


# ── HTML ↔ テキスト変換 ────────────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    """HTMLをプレーンテキストに変換（構造保持）"""
    soup = BeautifulSoup(html, "html.parser")
    # ディスクレーマーは除去してClaudeに渡す
    for tag in soup.select(".disclaimer"):
        tag.decompose()
    return soup.get_text(separator="\n")


def html_to_readable(html: str) -> str:
    """HTMLをある程度マークアップを残したテキストに変換（Claudeへの入力用）"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select(".disclaimer"):
        tag.decompose()

    lines = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "ul", "ol"]):
        tag = element.name
        text = element.get_text(strip=True)
        if not text:
            continue
        if tag in ("h1", "h2"):
            lines.append(f"\n## {text}")
        elif tag == "h3":
            lines.append(f"\n### {text}")
        elif tag == "h4":
            lines.append(f"\n#### {text}")
        elif tag == "li":
            lines.append(f"- {text}")
        elif tag == "p":
            lines.append(text)

    return "\n".join(lines)


def markdown_to_html(md_text: str) -> str:
    """マークダウンをWordPress用HTMLに変換"""
    # H1タグとWordPressコメントを除去
    lines = [l for l in md_text.splitlines() if not l.startswith("# ") and "<!--" not in l]
    cleaned = "\n".join(lines)
    html = md_converter.markdown(cleaned, extensions=["extra", "nl2br"])
    return html


# ── Step 1: ファクトチェック ───────────────────────────────────────────────────

FACTCHECK_PROMPT = """以下はITキャリア・プログラミング・副業・投資ジャンルのアフィリエイトブログ記事です。

2026年現在の技術動向・市場データ・業界の実態と照合し、以下の観点でファクトチェックしてください：

チェック観点：
1. 技術的な事実の誤り（バージョン・仕様・業界標準の誤記）
2. 統計・数値の誤り・出典不明の断定（根拠のない数字）
3. 時代遅れになった情報（ライブラリ・ツール・サービスの旧情報）
4. 個人体験・個人見解として書かれている箇所（「私は〜」「私が〜」「私自身〜」「知人の〜」「周りの〜」など）
5. 誇大表現・根拠のない断定（「必ず〜できる」「〜はずです」「〜間違いない」など）
6. 特定年のレポート・調査の引用（「2023年調査」「2024年版」など、現時点より古い年が入っている）
7. 法律・税制・制度・料金に関する断定的な記述（変更リスクが高い箇所）
8. 最上級表現の根拠不足（「唯一の〜」「最も〜な言語」など、根拠なく断定している）

問題がある箇所をJSONで返してください。問題がなければ空配列 [] を返してください。

出力形式（JSONのみ、コードブロック不要）:
[
  {{
    "id": 1,
    "type": "事実誤り|時代遅れ|個人体験|誇大表現|数値未確認|年号古い|制度変更リスク|最上級根拠不足",
    "original": "元の問題箇所（30字程度）",
    "issue": "何が問題か（1〜2文）",
    "fix": "推奨される修正表現または修正方針"
  }}
]

記事本文:
{article_text}"""


def run_factcheck(client: anthropic.Anthropic, article_text: str) -> list[dict]:
    """Claude Haiku でファクトチェックを実行し問題点リストを返す"""
    prompt = FACTCHECK_PROMPT.format(article_text=article_text[:6000])  # token上限対策

    message = client.messages.create(
        model=MODEL_FACTCHECK,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # JSON配列を正規表現で抽出（コードブロック・前後テキスト対応）
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if not match:
        print(f"  ⚠ ファクトチェックJSONが見つかりません。問題なしとして続行。")
        return []

    json_str = match.group(0)
    try:
        issues = json.loads(json_str)
        return issues if isinstance(issues, list) else []
    except json.JSONDecodeError as e:
        print(f"  ⚠ ファクトチェックJSONパース失敗: {e}")
        print(f"  Raw: {json_str[:300]}")
        return []


# ── Step 2: リライト ───────────────────────────────────────────────────────────

REWRITE_PROMPT = """以下のブログ記事を、ファクトチェックの指摘を反映してリライトしてください。

【必須ルール】
- 指摘された問題点をすべて修正すること

■ 個人体験・個人見解の除去
- 「私は〜」「私が〜」「私自身〜」「知人の〜」「周りの〜」など個人体験・個人見解に基づく表現をすべて削除すること
- 代替表現：「〜という声が多い」「〜という事例が報告されている」「業界では〜が一般的とされている」「〜と言われることが多い」など客観的な三人称表現に置き換えること
- リライト後に新たな個人体験表現を追加しないこと

■ 数値・引用の扱い
- 具体的な数値・統計には「公式情報によると」「〜の調査によると」など出典を添えること
- 調査・レポートを引用する際は特定の年（「2024年版」「2023年調査」など）を記載しないこと。代わりに「○○の年次調査によると」「○○の最新レポートによると」のように年を省いた表現にすること
- 料金・価格・費用は断定せず「（公式サイトで要確認）」を添えること

■ 断定・誇大表現の緩和
- 「必ず〜できます」「〜はずです」「〜間違いない」などの根拠のない断定表現は「〜するケースが多い」「〜が期待できます」などに緩めること
- 「最も〜」「唯一の〜」などの最上級・排他表現は根拠がある場合のみ使用し、ない場合は「有数の〜」「広く使われている〜」「主要な〜」などに置き換えること

■ 構成・SEO・見出し階層
- 記事の構成・ボリューム・見出し構成は原則維持すること
- SEOキーワードが含まれる見出し（H2・H3）は文言を変更しないこと
- 見出し階層（H1〜H4）を元記事と完全に一致させること。元記事で H4（####）として書かれている項目名（ツール名・サービス名・手順タイトルなど）は、リライト後も必ず #### で記述すること。平文や太字（**〜**）に変換しないこと
- 見出し直後の本文は見出しと同じインデントレベルで続けること。見出しレベルを勝手に昇格・降格させないこと
- 出力はマークダウン形式のみ（H1タイトル行から始める）

【ファクトチェック指摘一覧】
{issues_text}

【元記事】
{article_text}

リライト済み記事（マークダウン）を出力してください:"""


def run_rewrite(client: anthropic.Anthropic, article_text: str, issues: list[dict]) -> str:
    """Claude Sonnet でリライトを実行しマークダウンを返す"""
    if issues:
        issues_text = "\n".join(
            f"{i.get('id','?')}. [{i.get('type','不明')}] 「{i.get('original', i.get('issue','')[:30])}」\n   問題：{i.get('issue','')}\n   修正案：{i.get('fix','')}"
            for i in issues
        )
    else:
        issues_text = "※ファクトチェック上の問題は検出されませんでしたが、個人体験表現の削除と表現品質の向上のみ行ってください。"

    prompt = REWRITE_PROMPT.format(
        issues_text=issues_text,
        article_text=article_text[:8000],  # token上限対策
    )

    message = client.messages.create(
        model=MODEL_REWRITE,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


# ── Step 3: アイキャッチ画像生成 ──────────────────────────────────────────────────

EYECATCH_PROMPT = """You are a professional visual designer and illustrator. Your task is to generate a high-quality, professional eye-catch image that serves as the primary visual representation for an article.

**Style and Approach:**
The visual style must be professional, clean, and highly relevant to the subject matter. Use a "concrete" artistic approach—this means the image should depict real-world objects, settings, or scenarios described in the text rather than using abstract shapes, metaphors, or symbolic patterns. The goal is a clear, meaningful representation that a reader can immediately associate with the topic. Avoid cluttered compositions or meaningless decorative elements. The lighting should be professional, and the colors should be balanced and appealing for a high-end webpage layout.

**Image Description:**
Create a detailed visual representation based on the following article content:
<article_text>{article_text}</article_text>

The image should focus on the most important themes or subjects mentioned in the text. If the article is about technology, show specific modern devices in a clean environment; if it is about nature, show a specific, vivid landscape; if it is about a professional service, show people in a relevant, realistic workplace setting. Ensure the subject is the central focal point and the background supports the narrative without being distracting.

**Constraints:**
- Do not include any text, letters, or words within the image.
- Avoid abstract or surreal designs.
- Ensure the image is concrete and direct.
- Maintain a professional, editorial quality suitable for a header image."""


def generate_eyecatch(title: str, article_text: str) -> bytes | None:
    """Imagen 4 でアイキャッチ画像を生成し PNG バイナリを返す"""
    try:
        from google import genai
        from google.genai import types as genai_types

        gclient = genai.Client(api_key=GOOGLE_API_KEY)

        # 記事テキストを先頭1500字に絞ってプロンプト構築
        snippet = f"Title: {title}\n\n{article_text[:1500]}"
        prompt = EYECATCH_PROMPT.format(article_text=snippet)

        response = gclient.models.generate_images(
            model=MODEL_IMAGE,
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
                person_generation="allow_adult",
            ),
        )

        if not response.generated_images:
            print("  ⚠ Imagen: 画像が生成されませんでした（安全フィルタ等）")
            return None

        return response.generated_images[0].image.image_bytes

    except Exception as e:
        print(f"  ⚠ アイキャッチ生成エラー: {e}")
        return None


def upload_media_to_wp(image_bytes: bytes, filename: str) -> int | None:
    """画像バイナリを WP メディアライブラリへアップロードし media_id を返す"""
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "image/png",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    res = requests.post(
        f"{WP_URL}/wp-json/wp/v2/media",
        headers=headers,
        data=image_bytes,
    )
    if res.status_code not in (200, 201):
        print(f"  ⚠ メディアアップロード失敗: HTTP {res.status_code} — {res.text[:200]}")
        return None
    media_id = res.json().get("id")
    print(f"  📎 メディアアップロード完了 (media_id: {media_id})")
    return media_id


# ── メイン処理 ─────────────────────────────────────────────────────────────────

def process_post(client: anthropic.Anthropic, post_id: int, dry_run: bool = False, no_eyecatch: bool = False, eyecatch_only: bool = False):
    print(f"\n{'='*60}")
    print(f"📄 投稿ID: {post_id}")

    # 1. 記事取得
    print("  [1/5] WordPress から下書き取得中...")
    post = fetch_post(post_id)
    title = post.get("title", {}).get("rendered", f"ID:{post_id}")
    raw_html = post.get("content", {}).get("raw", "") or post.get("content", {}).get("rendered", "")
    print(f"  タイトル: {title}")
    print(f"  ステータス: {post.get('status', '不明')}")

    if not raw_html:
        print("  ⚠ 本文が空です。スキップします。")
        return

    # 2. HTMLをテキストに変換
    article_text = html_to_readable(raw_html)

    # アイキャッチのみモード
    if eyecatch_only:
        if no_eyecatch or not GOOGLE_API_KEY:
            print("  ⚠ アイキャッチ生成をスキップ")
            return
        print("  [2/3] アイキャッチ画像生成中... (Imagen 4)")
        image_bytes = generate_eyecatch(title, article_text)
        if image_bytes and not dry_run:
            safe_title = re.sub(r'[^\x00-\x7F]', '', title)
            safe_title = re.sub(r'[^\w\-]', '_', safe_title)[:40].strip('_')
            filename = f"eyecatch_{post_id}_{safe_title}.png"
            media_id = upload_media_to_wp(image_bytes, filename)
            if media_id:
                print("  [3/3] featured_media を設定中...")
                res = requests.post(
                    f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
                    headers=get_wp_headers(),
                    json={"featured_media": media_id},
                )
                if res.status_code in (200, 201):
                    print(f"  ✅ アイキャッチ設定完了 (media_id:{media_id})")
                else:
                    print(f"  ⚠ featured_media 設定失敗: HTTP {res.status_code}")
        elif dry_run:
            print("  --dry-run: アップロードスキップ（生成は成功）")
        print("  完了 ✔")
        return

    # 3. ファクトチェック
    print("  [2/5] ファクトチェック実行中... (Claude Haiku)")
    issues = run_factcheck(client, article_text)

    if issues:
        print(f"  ⚡ {len(issues)}件の指摘:")
        for issue in issues:
            print(f"     [{issue.get('type', '?')}] {issue.get('original', '')[:40]}")
    else:
        print("  ✅ ファクトチェック上の主要な問題は検出されませんでした")

    # 4. リライト
    print("  [3/5] リライト実行中... (Claude Sonnet)")
    rewritten_md = run_rewrite(client, article_text, issues)

    # 5. マークダウン → HTML
    new_html = markdown_to_html(rewritten_md)

    # ディスクレーマーを末尾に再付加
    disclaimer = (
        '\n<div class="disclaimer" style="margin-top:2em;padding:1em;background:#f5f5f5;'
        'border-left:4px solid #ccc;font-size:.9em;color:#555;">'
        '※本記事の情報は執筆時点のものです。料金・サービス内容・制度は変更される場合があります。'
        '最新情報は各公式サイトにてご確認ください。</div>'
    )
    new_html += disclaimer

    # 5. アイキャッチ生成
    media_id = None
    if not no_eyecatch and GOOGLE_API_KEY:
        print("  [4/5] アイキャッチ画像生成中... (Imagen 3)")
        image_bytes = generate_eyecatch(title, article_text)
        if image_bytes and not dry_run:
            safe_title = re.sub(r'[^\x00-\x7F]', '', title)  # 非ASCII除去
            safe_title = re.sub(r'[^\w\-]', '_', safe_title)[:40].strip('_')
            filename = f"eyecatch_{post_id}_{safe_title}.png"
            media_id = upload_media_to_wp(image_bytes, filename)
        elif image_bytes and dry_run:
            print("  [4/5] --dry-run モード: 画像アップロードはスキップ（生成は成功）")
    elif no_eyecatch:
        print("  [4/5] --no-eyecatch: アイキャッチ生成をスキップ")
    else:
        print("  [4/5] GOOGLE_API_KEY 未設定: アイキャッチ生成をスキップ")

    # 6. WP更新
    if dry_run:
        print("  [5/5] --dry-run モード: WP更新はスキップします")
        print("\n  ── リライト結果プレビュー（先頭500字）──")
        print(rewritten_md[:500])
        print("  ...")
    else:
        print("  [5/5] WordPress 下書きを更新中...")
        payload: dict = {"content": new_html}
        if media_id:
            payload["featured_media"] = media_id
        res = requests.post(
            f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
            headers=get_wp_headers(),
            json=payload,
        )
        if res.status_code not in (200, 201):
            raise RuntimeError(f"投稿更新失敗: HTTP {res.status_code} — {res.text[:200]}")
        updated = res.json()
        eyecatch_status = f"アイキャッチ設定済み (media_id:{media_id})" if media_id else "アイキャッチなし"
        print(f"  ✅ 更新完了: {updated.get('link', '(URLなし)')} [{eyecatch_status}]")

    print(f"  完了 ✔")


def main():
    parser = argparse.ArgumentParser(description="WordPress下書きをファクトチェック→リライト→アイキャッチ生成")
    parser.add_argument("post_ids", nargs="+", type=int, help="対象の投稿ID（複数可）")
    parser.add_argument("--dry-run", action="store_true", help="WP更新なし（確認用）")
    parser.add_argument("--no-eyecatch", action="store_true", help="アイキャッチ画像生成をスキップ")
    parser.add_argument("--eyecatch-only", action="store_true", help="アイキャッチ生成・設定のみ（リライトスキップ）")
    args = parser.parse_args()

    # 環境変数チェック
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "WP_URL": WP_URL,
        "WP_USER": WP_USER,
        "WP_APP_PASSWORD": WP_APP_PASSWORD,
    }.items() if not v]
    if missing:
        print(f"❌ 環境変数が未設定です: {', '.join(missing)}")
        sys.exit(1)

    if not GOOGLE_API_KEY and not args.no_eyecatch:
        print("⚠ GOOGLE_API_KEY が未設定です。アイキャッチ生成はスキップされます。")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    print(f"🔍 対象投稿ID: {args.post_ids}")
    print(f"{'🔵 dry-run モード（WP更新なし）' if args.dry_run else '🟢 通常モード（下書き更新あり）'}")
    print(f"🖼  アイキャッチ: {'スキップ (--no-eyecatch)' if args.no_eyecatch else ('生成あり (Imagen 3)' if GOOGLE_API_KEY else 'スキップ (APIキー未設定)')}")

    for post_id in args.post_ids:
        try:
            process_post(client, post_id, dry_run=args.dry_run, no_eyecatch=args.no_eyecatch, eyecatch_only=args.eyecatch_only)
        except Exception as e:
            import traceback
            print(f"  ❌ エラー (ID:{post_id}): {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"全{len(args.post_ids)}件の処理が完了しました。")


if __name__ == "__main__":
    main()
