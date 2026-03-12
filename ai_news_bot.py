"""
AI News Daily Bot
毎朝8時(JST)にAI関連ニュースを収集し、マークダウン形式でBoxに保存するスクリプト

【Box OAuth2 Refresh Token自動更新方式】
  - 実行のたびにRefresh TokenでAccess Tokenを取得
  - 新しいRefresh TokenをGitHub Secrets(PAT経由)に自動書き戻し
  - これにより毎日自動実行しても永続的に動作する

【必要な環境変数（GitHub Secrets）】
  ANTHROPIC_API_KEY     : Anthropic APIキー（必須）
  BOX_CLIENT_ID         : BoxアプリのClient ID（必須）
  BOX_CLIENT_SECRET     : BoxアプリのClient Secret（必須）
  BOX_REFRESH_TOKEN     : Box OAuth2 Refresh Token（必須・自動更新）
  BOX_ARCHIVE_FOLDER_ID : 保存先フォルダID（デフォルト: 370318355595）
  GH_PAT                : GitHub Personal Access Token・secrets書き込み権限付き（必須）
  NEWS_API_KEY          : NewsAPI キー（任意）

  ※ GITHUB_REPOSITORY はGitHub Actionsが自動提供
"""

import os
import base64
import requests
from datetime import datetime, timedelta, timezone
import anthropic

JST = timezone(timedelta(hours=9))
BOX_ARCHIVE_FOLDER_ID = os.environ.get("BOX_ARCHIVE_FOLDER_ID", "370318355595")


# ---- Box OAuth2: Access Token取得 & Refresh Token自動更新 ----
def get_box_access_token() -> str:
    """
    Refresh TokenでAccess Tokenを取得し、
    新しいRefresh TokenをGitHub Secrets(GH_PAT経由)に書き戻す。
    """
    res = requests.post(
        "https://api.box.com/oauth2/token",
        data={
            "grant_type":    "refresh_token",
            "refresh_token": os.environ["BOX_REFRESH_TOKEN"],
            "client_id":     os.environ["BOX_CLIENT_ID"],
            "client_secret": os.environ["BOX_CLIENT_SECRET"],
        },
        timeout=10,
    )
    res.raise_for_status()
    data              = res.json()
    access_token      = data["access_token"]
    new_refresh_token = data["refresh_token"]
    print("✅ Box Access Token 取得完了")

    # 新しいRefresh TokenをGitHub Secretsに書き戻す
    _update_github_secret("BOX_REFRESH_TOKEN", new_refresh_token)

    return access_token


def _update_github_secret(name: str, value: str) -> None:
    """GitHub PAT経由でSecretsを更新する。"""
    from nacl import encoding, public

    pat  = os.environ["GH_PAT"]  # Personal Access Token (secrets:write権限付き)
    repo = os.environ["GITHUB_REPOSITORY"]
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # リポジトリ公開鍵を取得
    res = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers, timeout=10,
    )
    res.raise_for_status()
    key_data = res.json()

    # libsodiumで暗号化
    pk        = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder())
    box       = public.SealedBox(pk)
    encrypted = base64.b64encode(box.encrypt(value.encode())).decode()

    # Secretを更新
    res = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        headers=headers,
        json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
        timeout=10,
    )
    res.raise_for_status()
    print(f"✅ GitHub Secret [{name}] 自動更新完了")


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


# ---- ③ Box に保存 ----
def save_to_box(markdown: str, filename: str, access_token: str) -> str:
    content = markdown.encode("utf-8")
    headers = {"Authorization": f"Bearer {access_token}"}
    attrs   = f'{{"name":"{filename}","parent":{{"id":"{BOX_ARCHIVE_FOLDER_ID}"}}}}'

    res = requests.post(
        "https://upload.box.com/api/2.0/files/content",
        headers=headers,
        files={
            "attributes": (None, attrs, "application/json"),
            "file":       (filename, content, "text/markdown"),
        },
        timeout=30,
    )

    # 409: 同名ファイルあり → Search APIでfile_idを取得して上書き
    if res.status_code == 409:
        print("   同名ファイルあり → 上書き中...")
        search_res = requests.get(
            "https://api.box.com/2.0/search",
            headers=headers,
            params={
                "query": filename,
                "ancestor_folder_ids": BOX_ARCHIVE_FOLDER_ID,
                "type": "file",
                "limit": 5,
            },
            timeout=10,
        )
        search_res.raise_for_status()
        file_id = next(
            (e["id"] for e in search_res.json().get("entries", []) if e["name"] == filename),
            None,
        )
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
            # 見つからない場合は時刻サフィックスをつけて新規保存
            ts       = datetime.now(JST).strftime("%H%M%S")
            filename = filename.replace(".md", f"_{ts}.md")
            attrs    = f'{{"name":"{filename}","parent":{{"id":"{BOX_ARCHIVE_FOLDER_ID}"}}}}'
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

    print("🔑 Box Access Token 取得中...")
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
