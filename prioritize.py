"""
未処理キーワードをClaudeでスコアリングし、上位N件に絞ってkeywords.csvを更新する
"""
import anthropic
import csv
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
KEYWORDS_CSV = "keywords.csv"
OUTPUT_CSV = "keywords.csv"
BATCH_SIZE = 50
TOP_N = 200  # 未処理から残す件数


def load_keywords():
    with open(KEYWORDS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_done(keyword: str) -> bool:
    safe = keyword.replace(" ", "_").replace("/", "_")
    return Path(f"articles/{safe}.md").exists()


def score_batch(client, batch: list[dict]) -> list[dict]:
    """50件ずつClaudeにスコアリングさせる"""
    keyword_list = "\n".join(
        f'{i+1}. [{row["カテゴリ"]}] {row["キーワード"]}'
        for i, row in enumerate(batch)
    )

    prompt = f"""以下のキーワード一覧について、アフィリエイトブログ（ITキャリア・プログラミング・副業・投資ジャンル）の観点から各キーワードをスコアリングしてください。

評価軸：
- 収益ポテンシャル（高単価アフィリエイト案件との親和性）
- 検索需要の大きさ（読者層の厚さ）
- 記事化したときのコンバージョンへの近さ

各キーワードを1〜10点でスコアリングし、以下のJSON形式のみで返してください：
[{{"rank": 1, "score": 8}}, {{"rank": 2, "score": 5}}, ...]

キーワード一覧：
{keyword_list}"""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # JSONブロックを抽出
    if "```" in raw:
        raw = raw.split("```")[1].replace("json", "").strip()

    scores = json.loads(raw)
    results = []
    for s in scores:
        idx = s["rank"] - 1
        if idx < len(batch):
            results.append({**batch[idx], "_score": s["score"]})
    return results


def main():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    keywords = load_keywords()

    done = [row for row in keywords if is_done(row["キーワード"])]
    todo = [row for row in keywords if not is_done(row["キーワード"])]

    print(f"生成済み: {len(done)}件（そのまま保持）")
    print(f"未処理: {len(todo)}件 → 上位{TOP_N}件に絞ります")
    print()

    # バッチ処理
    scored = []
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        print(f"スコアリング中... {i+1}〜{i+len(batch)}件目")
        try:
            results = score_batch(client, batch)
            scored.extend(results)
        except Exception as e:
            print(f"  エラー: {e} → スコア5で継続")
            for row in batch:
                scored.append({**row, "_score": 5})

    # スコア順ソート → 上位TOP_N件
    scored.sort(key=lambda x: x["_score"], reverse=True)
    top = scored[:TOP_N]

    print(f"\nスコアリング完了。上位{TOP_N}件を選定しました。")
    print(f"カット: {len(scored) - TOP_N}件")
    print()

    # スコア分布を表示
    for threshold in [9, 8, 7, 6, 5]:
        count = sum(1 for r in top if r["_score"] >= threshold)
        print(f"  スコア{threshold}以上: {count}件")

    # keywords.csvを更新（生成済み + 上位TOP_N件、番号を振り直し）
    final = done + [{k: v for k, v in row.items() if k != "_score"} for row in top]
    for i, row in enumerate(final, 1):
        row["番号"] = str(i)

    fieldnames = ["番号", "キーワード", "軸", "カテゴリ"]
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(final)

    print(f"\nkeywords.csv を更新しました: 合計{len(final)}件")
    print(f"  生成済み {len(done)}件 + 未処理上位 {len(top)}件")


if __name__ == "__main__":
    main()
