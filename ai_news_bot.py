"""
AI News Daily Bot
毎朝8時(JST)にAI関連ニュースを収集し、マークダウン形式でBoxに保存するスクリプト

【Box JWT認証方式】
  - Private Keyを使ってJWTトークンを自己署名
  - Refresh Token不要・永続的に動作

【必要な環境変数（GitHub Secrets）】
  ANTHROPIC_API_KEY       : Anthropic APIキー（必須）
  BOX_CLIENT_ID           : BoxアプリのClient ID（必須）
  BOX_CLIENT_SECRET       : BoxアプリのClient Secret（必須）
  BOX_ENTERPRISE_ID       : BoxのEnterprise ID（必須）
  BOX_PUBLIC_KEY_ID       : JWTの公開鍵ID（必須）
  BOX_PRIVATE_KEY         : JWTの秘密鍵（必須）
  BOX_PRIVATE_KEY_PASSPHRASE : 秘密鍵のパスフレーズ（必須）
  BOX_USER_ID             : サービスアカウントのユーザーID（必須）
  BOX_ARCHIVE_FOLDER_ID   : 保存先フォルダID（必須）
  NEWS_API_KEY            : NewsAPI キー（任意）
"""

import os
import time
import uuid
import requests
import jwt  # PyJWT
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from datetime import datetime, timedelta, timezone
import anthropic

JST = timezone(timedelta(hours=9))
BOX_ARCHIVE_FOLDER_ID = os.environ.get("BOX_ARCHIVE_FOLDER_ID", "370834602129")


# ---- Box JWT: Access Token を取得 ----
def get_box_access_token() -> str:
    client_id     = os.environ["BOX_CLIENT_ID"]
    client_secret = os.environ["BOX_CLIENT_SECRET"]
    enterprise_id = os.environ["BOX_ENTERPRISE_ID"]
    public_key_id = os.environ["BOX_PUBLIC_KEY_ID"]
    private_key   = os.environ["BOX_PRIVATE_KEY"]
    if "\\n" in private_key:
        private_key = private_key.replace("\\n", "\n")
    private_key = private_key.strip()
    if not private_key.endswith("\n"):
        private_key = private_key + "\n"
    passphrase = os.environ["BOX_PRIVATE_KEY_PASSPHRASE"].encode()

    key = load_pem_private_key(private_key.encode(), password=passphrase)
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": enterprise_id,
        "box_sub_type": "enterprise",
        "aud": "https://api.box.com/oauth2/token",
        "jti": str(uuid.uuid4()),
        "exp": now + 45,
    }
    assertion = jwt.encode(claims, key, algorithm="RS256", headers={"kid": public_key_id})

    res = requests.post(
        "https://api.box.com/oauth2/token",
        data={
            "grant_type":    "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion":     assertion,
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    res.raise_for_status()
    print("✅ Box Access Token 取得完了（JWT）")
    return res.json()["access_token"]


# ---- ① ニュース収集 (NewsAPI) ----
def fetch_ai_news_via_newsapi() -> list[dict] | None:
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        print("   ℹ️ NEWS_API_KEY 未設定 → Claude web_search を使用")
        return None
    print(f"   ℹ️ NEWS_API_KEY 確認済み（末尾4文字: ...{api_key[-4:]}）")

    # 過去2日分を取得（当日分が遅延反映されることがあるため）
    two_days_ago = (datetime.now(JST) - timedelta(days=2)).strftime("%Y-%m-%d")
    url = (
        "https://newsapi.org/v2/everything"
        "?q=AI OR LLM OR 生成AI OR ChatGPT OR Claude OR Gemini OR OpenAI OR Anthropic"
        f"&from={two_days_ago}&sortBy=publishedAt&pageSize=20&language=en&apiKey={api_key}"
    )
    print(f"   🔍 検索期間: {two_days_ago} 〜")
    try:
        res = requests.get(url, timeout=10)
        print(f"   📡 NewsAPI応答: {res.status_code}")
        if not res.ok:
            print(f"   ⚠️ NewsAPI エラー: {res.status_code} | {res.text[:200]}")
            return None
        data = res.json()
        print(f"   📰 NewsAPI status: {data.get(chr(115)+chr(116)+chr(97)+chr(116)+chr(117)+chr(115))} | 件数: {data.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(82)+chr(101)+chr(115)+chr(117)+chr(108)+chr(116)+chr(115))}")
        articles = data.get("articles", [])
        result = [
            {
                "title": a["title"],
                "url": a["url"],
                "source": a["source"]["name"],
                "description": a.get("description", ""),
            }
            for a in articles[:15]
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
        print(f"   ✅ 有効記事数: {len(result)}")
        return result if result else None
    except Exception as e:
        print(f"   ⚠️ NewsAPI 例外: {type(e).__name__}: {e}")
        return None


# ---- ② Claude Haiku でマークダウン生成 ----
def generate_markdown(articles: list[dict] | None) -> str:
    client   = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    today_jp = datetime.now(JST).strftime("%Y年%m月%d日")
    now_str  = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    system_context = """あなたはBox Japan・エンタープライズSaaS領域の専門アナリストです。

【読者】
- Box Consultingのコンサルタント・SAチーム（一次読者）
- エンタープライズ企業のIT担当者・経営層（共有先）

【評価の4軸】
1. Box事業への影響：Box AI・競合SaaS（M365/SharePoint/Google Drive/Dropbox）との差別化に関わるか
2. 顧客提案への活用：日本の大手企業との商談・提案資料で使えるトピックか
3. 日本市場での実用性：日本語対応・日本企業の導入事例・国内規制に関わるか
4. 技術トレンドの重要度：LLM新モデル・AI Agent・RAGなどエンタープライズ活用に直結するか"""

    output_format = f"""【出力フォーマット（厳守）】
# AI & SaaS Daily — {today_jp}

## 1. [ニュースタイトル（日本語・30文字以内）]
**概要:** 何が起きたか4文以内で端的に。
**Box/提案への示唆:** 商談・提案で使えるポイント、またはBoxへの影響を1〜2文で。
**ソース:** [媒体名](URL)

（## 2. 〜 ## 5. も同じ形式）

---
### 今日のポイント
Box Consultingとして今週・今月注目すべきトレンドを3〜4行で総括。
「〇〇という流れが加速しており、□□な提案機会につながる」という形式で締める。

---
*{now_str} / Box Consulting AI Digest*"""

    if articles:
        articles_text = "\n".join(
            f"- {a['title']} ({a['source']})\n  概要: {a['description']}\n  URL: {a['url']}"
            for a in articles
        )
        prompt = f"""以下は{today_jp}の過去24時間のAI・SaaS関連ニュース一覧です。

{system_context}

【選定基準（優先順）】
1. Box・競合SaaSのAI機能・戦略に直接影響する発表
2. エンタープライズ向けLLM新モデル・新機能リリース
3. 日本企業のAI導入事例・業務自動化ユースケース
4. AI規制・データガバナンス・セキュリティ動向（日本・EU・米国）
5. Microsoft Copilot / Google Workspace AI など競合エコシステムの動向

ニュース一覧:
{articles_text}

### 🔗 収集元ニュース一覧
（上記ニュース一覧の全件をリスト形式で末尾に記載）

{output_format}"""

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
    else:
        # NewsAPIなし → web_search でニュース収集も兼ねる
        prompt = f"""今日（{today_jp}）の過去24時間のAI・SaaS関連ニュースを調査し、まとめてください。

{system_context}

【調査対象（優先順）】
1. Box / OneDrive / SharePoint / Google Drive / Dropbox のAI機能・戦略発表
2. OpenAI / Anthropic / Google / Microsoft / Meta のエンタープライズ向けAI動向
3. 日本企業のAI導入・活用事例（製造・金融・商社・流通など大手企業）
4. AI規制・データガバナンス・個人情報保護（日本・EU・米国）
5. エンタープライズ向けAI Agent・RAG・セキュリティ関連動向

{output_format}"""

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=3000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )

    return "\n".join(
        block.text for block in message.content if block.type == "text"
    ).strip()


# ---- ③ Box に保存 ----
def save_to_box(markdown: str, filename: str, access_token: str) -> str:
    content = markdown.encode("utf-8")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "As-User": os.environ["BOX_USER_ID"],
    }
    attrs = f'{{"name":"{filename}","parent":{{"id":"{BOX_ARCHIVE_FOLDER_ID}"}}}}'

    res = requests.post(
        "https://upload.box.com/api/2.0/files/content",
        headers=headers,
        files={
            "attributes": (None, attrs, "application/json"),
            "file":       (filename, content, "text/markdown"),
        },
        timeout=30,
    )
    print(f"   Box upload response: {res.status_code} | {res.text[:300]}")

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

    if not res.ok:
        print(f"   ⚠️ Box APIレスポンス ({res.status_code}): {res.text[:300]}")
    res.raise_for_status()
    file_id  = res.json()["entries"][0]["id"]
    file_url = f"https://app.box.com/file/{file_id}"
    print(f"✅ Box保存完了: {file_url}")
    return file_url


# ---- メイン ----
def main():
    print(f"📰 AI News Bot 開始: {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")

    print("🔑 Box Access Token 取得中（JWT）...")
    access_token = get_box_access_token()

    print("🔍 ニュース収集中...")
    articles = fetch_ai_news_via_newsapi()
    source = "NewsAPI" if articles else "Claude web_search"
    print(f"   ソース: {source} | 件数: {len(articles) if articles else 'N/A'}")

    print("✍️  マークダウン生成中（Claude Haiku）...")
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
