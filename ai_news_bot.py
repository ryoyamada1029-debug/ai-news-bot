"""
AI News Daily Bot
毎朝8時(JST)にAI関連ニュースを収集し、マークダウン形式でBoxに保存するスクリプト

【必要な環境変数】
  ANTHROPIC_API_KEY  : Anthropic APIキー（必須）
  BOX_ACCESS_TOKEN   : Box Developer Token または OAuth2 Access Token（必須）
  NEWS_API_KEY       : NewsAPI キー（任意）
  BOX_ARCHIVE_FOLDER_ID : 保存先フォルダID（デフォルト: 370318355595）

【Box フォルダ構成】
AI News Archive/
  └── 2026-03-12_ai_news.md
  └── 2026-03-13_ai_news.md  ...
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone
import anthropic

JST = timezone(timedelta(hours=9))
BOX_ARCHIVE_FOLDER_ID = os.environ.get("BOX_ARCHIVE_FOLDER_ID", "370318355595")


# ---- ① ニュース収集 (NewsAPI) ----
def fetch_ai_news_via_newsapi() -> list[dict] | None:
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        return None

    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")
    url = (
        "https://newsapi.org/v2/everything"
        "?q=AI OR 人工知能 OR LLM OR 生成AI OR ChatGPT OR Claude OR Gemini"
        f"&from={yesterday}&sortBy=popularity&pageSize=20&apiKey={api_key}"
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


# ---- ② Claude でマークダウン生成 ----
def generate_markdown(articles: list[dict] | None) -> str:
    client   = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today_jp = datetime.now(JST).strftime("%Y年%m月%d日")
    now_str  = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    if articles:
        articles_text = "\n".join(
            f"- {a['title']} ({a['source']})\n  概要: {a['description']}\n  URL: {a['url']}"
            for a in articles
        )
        prompt = f"""以下は{today_jp}の過去24時間のAI関連ニュース一覧です。

日本のビジネスパーソン（エンタープライズIT担当者）向けに、
重要度・インパクト順にTop5を厳選し、マークダウン形式で日本語まとめを作成してください。

【選定基準】
- LLM / 生成AIの新機能・新モデルリリース
- 大企業のAI戦略・導入事例
- AI規制・政策動向
- セキュリティ・リスク関連

ニュース一覧:
{articles_text}

【出力フォーマット（厳守）】
# AI Daily News — {today_jp}

## 1. [ニュースタイトル（日本語）]
**要約:** 2〜3行の要約。ビジネスへの影響を含めて。
**ソース:** [ソース名](URL)

## 2. ...（以下同様、5件まで）

---
## 収集元ニュース一覧
1. [タイトル](URL) — ソース名

---
*生成日時: {now_str} / Powered by Claude AI*"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
    else:
        prompt = f"""今日（{today_jp}）の過去24時間のAI・生成AIニュースTop5を調べ、
日本のビジネスパーソン（エンタープライズIT担当者）向けにマークダウン形式でまとめてください。

【調査対象】
- OpenAI / Anthropic / Google / Microsoft / Meta のAI動向
- 日本企業のAI導入・活用事例
- AI規制・政策（日本・EU・米国）
- LLM新モデル・新機能リリース

【出力フォーマット（厳守）】
# AI Daily News — {today_jp}

## 1. [ニュースタイトル（日本語）]
**要約:** 2〜3行の要約。ビジネスへの影響を含めて。
**ソース:** [ソース名](URL)

## 2. ...（以下同様、5件まで）

---
*生成日時: {now_str} / Powered by Claude AI*"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

    return "\n".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


# ---- ③ Box API に直接アップロード ----
def save_to_box(markdown: str) -> str | None:
    """
    Box Content API を直接呼んでファイルをアップロードする。
    Box MCPは使わない（GitHub Actionsから認証できないため）。
    """
    token    = os.environ.get("BOX_ACCESS_TOKEN")
    if not token:
        print("⚠️ BOX_ACCESS_TOKEN が未設定です")
        return None

    today    = datetime.now(JST).strftime("%Y-%m-%d")
    filename = f"{today}_ai_news.md"
    content  = markdown.encode("utf-8")

    # multipart/form-data でアップロード
    url = "https://upload.box.com/api/2.0/files/content"
    headers = {"Authorization": f"Bearer {token}"}
    attributes = f'{{"name": "{filename}", "parent": {{"id": "{BOX_ARCHIVE_FOLDER_ID}"}}}}'

    try:
        res = requests.post(
            url,
            headers=headers,
            files={
                "attributes": (None, attributes, "application/json"),
                "file":       (filename, content, "text/markdown"),
            },
            timeout=30,
        )

        # 同名ファイルが既にある場合は上書き（409 Conflict）
        if res.status_code == 409:
            file_id = res.json()["context_info"]["conflicts"][0]["id"]
            print(f"   同名ファイルが存在 → 上書き (file_id: {file_id})")
            res = requests.post(
                f"https://upload.box.com/api/2.0/files/{file_id}/content",
                headers=headers,
                files={
                    "attributes": (None, attributes, "application/json"),
                    "file":       (filename, content, "text/markdown"),
                },
                timeout=30,
            )

        res.raise_for_status()
        file_data = res.json()["entries"][0]
        file_id   = file_data["id"]
        file_url  = f"https://app.box.com/file/{file_id}"
        print(f"✅ Box保存完了: {file_url}")
        return file_url

    except Exception as e:
        print(f"⚠️ Box保存エラー: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"   レスポンス: {e.response.text[:300]}")
        return None


# ---- メイン ----
def main():
    print(f"📰 AI News Bot 開始: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")

    print("🔍 ニュース収集中...")
    articles = fetch_ai_news_via_newsapi()
    source = "NewsAPI" if articles else "Claude web_search"
    print(f"   ソース: {source} | 件数: {len(articles) if articles else 'N/A'}")

    print("✍️  マークダウン生成中...")
    markdown = generate_markdown(articles)
    print("--- 生成内容プレビュー ---")
    print(markdown[:400], "\n...")
    print("-------------------------")

    print("📦 Boxに保存中...")
    box_url = save_to_box(markdown)
    if not box_url:
        raise RuntimeError("Box保存に失敗しました")  # exit code 1 で通知

    print("🎉 完了!")


if __name__ == "__main__":
    main()
