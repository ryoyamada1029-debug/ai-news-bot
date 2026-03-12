"""
AI News Daily Bot
毎朝8時(JST)にAI関連ニュースを収集し、Slackに投稿するスクリプト
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone
import anthropic

JST = timezone(timedelta(hours=9))


# ---- ニュース収集 ----
def fetch_ai_news_via_newsapi() -> list[dict] | None:
    """NewsAPI.org（無料: 100req/日）でAIニュースを取得"""
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        return None

    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")
    url = (
        "https://newsapi.org/v2/everything"
        f"?q=AI OR 人工知能 OR LLM OR 生成AI OR ChatGPT OR Claude OR Gemini"
        f"&from={yesterday}"
        f"&sortBy=popularity"
        f"&pageSize=20"
        f"&apiKey={api_key}"
    )
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        articles = res.json().get("articles", [])
        return [
            {
                "title": a["title"],
                "url": a["url"],
                "source": a["source"]["name"],
                "description": a.get("description", ""),
            }
            for a in articles[:15]
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
    except Exception as e:
        print(f"⚠️ NewsAPI エラー: {e}")
        return None


# ---- Claude で要約・整形 ----
def summarize_with_claude(articles: list[dict] | None) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today_str = datetime.now(JST).strftime("%Y年%m月%d日")

    if articles:
        # NewsAPIからデータが取れた場合
        articles_text = "\n".join(
            [
                f"- {a['title']} ({a['source']})\n  概要: {a['description']}\n  URL: {a['url']}"
                for a in articles
            ]
        )
        prompt = f"""以下は{today_str}の過去24時間のAI関連ニュース一覧です。

日本のビジネスパーソン（エンタープライズIT担当者）向けに、
重要度・インパクト順にTop5を厳選し、各ニュースを日本語で要約してください。

【選定基準】
- LLM/生成AIの新機能・新モデルリリース
- 大企業のAI戦略・導入事例
- AI規制・政策動向
- セキュリティ・リスク関連

ニュース一覧:
{articles_text}

【出力フォーマット（厳守）】
🤖 *AI Daily News - {today_str}*

*1️⃣ [ニュースタイトル（日本語）]*
📝 [2〜3行の要約。ビジネスへの影響を含めて]
🔗 [URL]

（2〜5も同様）

---
_Powered by Claude AI • Box Consulting_"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

    else:
        # NewsAPIキーなし → Claude web_searchで検索
        prompt = f"""今日（{today_str}）の最新AI・生成AIニュースTop5を調べて、
日本のビジネスパーソン（エンタープライズIT担当者）向けにまとめてください。

【調査対象】
- OpenAI / Anthropic / Google / Microsoft / Meta のAI動向
- 日本企業のAI導入・活用事例
- AI規制・政策（日本・EU・米国）
- LLM新モデル・新機能リリース

【出力フォーマット（厳守）】
🤖 *AI Daily News - {today_str}*

*1️⃣ [ニュースタイトル（日本語）]*
📝 [2〜3行の要約。ビジネスへの影響を含めて]
🔗 [参照URL]

（2〜5も同様）

---
_Powered by Claude AI • Box Consulting_"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

    # text ブロックを結合して返す
    return "\n".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


# ---- Slack 投稿 ----
def post_to_slack(text: str) -> None:
    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    payload = {
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    res = requests.post(webhook_url, json=payload, timeout=10)
    res.raise_for_status()
    print("✅ Slack投稿完了")


# ---- メイン ----
def main():
    print(f"📰 AI News Bot 開始: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")

    print("🔍 ニュース収集中...")
    articles = fetch_ai_news_via_newsapi()
    source = "NewsAPI" if articles else "Claude web_search"
    print(f"   ソース: {source} | 件数: {len(articles) if articles else 'N/A'}")

    print("🤖 Claude で要約中...")
    summary = summarize_with_claude(articles)
    print("--- 生成テキスト ---")
    print(summary)
    print("-------------------")

    print("📤 Slack投稿中...")
    post_to_slack(summary)
    print("🎉 完了!")


if __name__ == "__main__":
    main()
