"""
AI News Daily Bot
毎朝8時(JST)にAI関連ニュースを収集し、マークダウン形式でBoxに保存するスクリプト

【Box CCG認証 + サービスアカウントのルートに保存】
  - CCGサービスアカウント自身のルートフォルダ(id="0")に保存
  - as-user不要・シンプル確実
  - 保存後、Boxの共有設定でRyoさんのアカウントからアクセス可能

【必要な環境変数（GitHub Secrets）】
  ANTHROPIC_API_KEY   : Anthropic APIキー（必須）
  BOX_CLIENT_ID       : Box CCGアプリのClient ID（必須）
  BOX_CLIENT_SECRET   : Box CCGアプリのClient Secret（必須）
  BOX_ENTERPRISE_ID   : BoxのEnterprise ID（必須）
  NEWS_API_KEY        : NewsAPI キー（任意）
"""

import os
import requests
from datetime import datetime, timedelta, timezone
import anthropic

JST = timezone(timedelta(hours=9))
BOX_FOLDER_ID = "0"  # CCGサービスアカウントのルートフォルダ


# ---- Box CCG: Access Token を取得 ----
def get_box_access_token() -> str:
    res = requests.post(
        "https://api.box.com/oauth2/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     os.environ["BOX_CLIENT_ID"],
            "client_secret": os.environ["BOX_CLIENT_SECRET"],
            "box_subject_type": "enterprise",
            "box_subject_id":   os.environ["BOX_ENTERPRISE_ID"],
        },
        timeout=10,
    )
    res.raise_for_status()
    print("✅ Box Access Token 取得完了（CCG）")
    return res.json()["access_token"]


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


# ---- ③ Box に保存（CCGのルートフォルダに直接） ----
def save_to_box(markdown: str, filename: str, access_token: str) -> str:
    content = markdown.encode("utf-8")
    headers = {"Authorization": f"Bearer {access_token}"}
    attrs   = f'{{"name":"{filename}","parent":{{"id":"{BOX_FOLDER_ID}"}}}}'

    # まず新規アップロード
    res = requests.post(
        "https://upload.box.com/api/2.0/files/content",
        headers=headers,
        files={
            "attributes": (None, attrs, "application/json"),
            "file":       (filename, content, "text/markdown"),
        },
        timeout=30,
    )
    print(f"   アップロード応答: {res.status_code}")

    # 409: 同名ファイルあり → 上書き
    if res.status_code == 409:
        print("   同名ファイルあり → 上書き中...")
        # レスポンスからfile_idを直接取得
        conflict_data = res.json()
        file_id = None
        try:
            file_id = conflict_data["context_info"]["conflicts"]["id"]
        except (KeyError, TypeError):
            pass
        if not file_id:
            try:
                file_id = conflict_data["context_info"]["conflicts"][0]["id"]
            except (KeyError, TypeError, IndexError):
                pass

        if file_id:
            res = requests.post(
                f"https://upload.box.com/api/2.0/files/{file_id}/content",
                headers=headers,
                files={
                    "attributes": (None, attrs, "application/json"),
                    "file":       (filename, content, "text/markdown"),
                },
                timeout=30,
            )
        else:
            # file_idが取れない場合は時刻サフィックスで新規保存
            ts       = datetime.now(JST).strftime("%H%M%S")
            filename = filename.replace(".md", f"_{ts}.md")
            attrs    = f'{{"name":"{filename}","parent":{{"id":"{BOX_FOLDER_ID}"}}}}'
            res = requests.post(
                "https://upload.box.com/api/2.0/files/content",
                headers=headers,
                files={
                    "attributes": (None, attrs, "application/json"),
                    "file":       (filename, content, "text/markdown"),
                },
                timeout=30,
            )

    res.raise_for_status()
    file_id  = res.json()["entries"][0]["id"]
    file_url = f"https://app.box.com/file/{file_id}"
    print(f"✅ Box保存完了: {file_url}")
    return file_url


# ---- メイン ----
def main():
    print(f"📰 AI News Bot 開始: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")

    print("🔑 Box Access Token 取得中（CCG）...")
    access_token = get_box_access_token()

    print("🔍 ニュース収集中...")
    articles = fetch_ai_news_via_newsapi()
    source = "NewsAPI" if articles else "Claude web_search"
    print(f"   ソース: {source} | 件数: {len(articles) if articles else 'N/A'}")

    print("✍️  マークダウン生成中...")
    markdown = generate_markdown(articles)
    print("--- 生成内容プレビュー ---")
    print(markdown[:400], "\n...")
    print("-------------------------")

    today    = datetime.now(JST).strftime("%Y-%m-%d")
    filename = f"{today}_ai_news.md"

    print("📦 Boxに保存中...")
    save_to_box(markdown, filename, access_token)

    print("🎉 完了!")


if __name__ == "__main__":
    main()
