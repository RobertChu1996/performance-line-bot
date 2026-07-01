import os
import re
import base64
import anthropic
from datetime import datetime, timedelta
from flask import Flask, request, abort, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage,
    TextSendMessage, ImageSendMessage,
)
from poster import generate_poster

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler      = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
GROUP_ID     = os.environ.get("LINE_GROUP_ID", "")
BASE_URL     = os.environ.get("BASE_URL", "").rstrip("/")

# In-memory buffer for report images (cleared after 結算)
pending_reports: list[str] = []  # base64-encoded images


@app.route("/health")
def health():
    return "OK"


@app.route("/static/posters/<filename>")
def serve_poster(filename):
    return send_from_directory("static/posters", filename)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    content = line_bot_api.get_message_content(event.message.id)
    image_data = b"".join(chunk for chunk in content.iter_content())
    pending_reports.append(base64.b64encode(image_data).decode())
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"✅ 已收到第 {len(pending_reports)} 張報表，傳完後請發「結算」"),
    )


@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()

    if hasattr(event.source, "group_id"):
        print(f"[GROUP ID] {event.source.group_id}", flush=True)

    # 結算：用 Claude 讀所有報表，找最高 FYC 者
    if text == "結算":
        if not pending_reports:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 尚未收到任何報表，請先傳報表照片"),
            )
            return

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        content = []
        for img_b64 in pending_reports:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
            })
        content.append({
            "type": "text",
            "text": (
                "這些是業績日報表，請找出所有業務員的姓名與台幣FYC金額，"
                "回傳台幣FYC最高的業務員姓名（只回傳中文姓名，不要其他文字）。"
                "如果有並列最高，只回傳其中一位即可。"
            ),
        })

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=50,
                messages=[{"role": "user", "content": content}],
            )
            winner_name = response.content[0].text.strip()
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"❌ 讀取報表失敗：{e}"),
            )
            return
        finally:
            pending_reports.clear()

        _announce(event, winner_name)
        return

    # 手動指令：業績王 姓名 [YYYY.MM.DD]
    match = re.match(r"業績王\s+(\S+)(?:\s+(\d{4}\.\d{2}\.\d{2}))?", text)
    if not match:
        return

    name     = match.group(1)
    date_str = match.group(2) or datetime.now().strftime("%Y.%m.%d")
    _announce(event, name, date_str)


def _announce(event, name: str, date_str: str = None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y.%m.%d")

    try:
        d            = datetime.strptime(date_str, "%Y.%m.%d")
        date_display = f"{d.month}/{d.day}"
        next_workday = d + timedelta(days=1)
        while next_workday.weekday() >= 5:
            next_workday += timedelta(days=1)
        next_day = f"{next_workday.month}/{next_workday.day}"
    except Exception:
        date_display = date_str
        next_day     = "下一個工作日"

    poster_path, title = generate_poster(name, date_str=date_str)

    if poster_path is None:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"❌ 找不到 {name} 的照片\n請將照片命名為「{name}職位.jpg」\n例：{name}業務主任.jpg"),
        )
        return

    filename  = os.path.basename(poster_path)
    image_url = f"{BASE_URL}/static/posters/{filename}"

    announcement = (
        f"恭喜{date_display}業績王\n"
        f"「{name} {title}」\n"
        f"請 {name} 於{next_day}晨會上台分享唷～\n"
        f"🇫🇷迎戰北法🇫🇷\n"
        f"Go!!Go!!Go🎉🎉🎉"
    )

    target = GROUP_ID or getattr(event.source, "group_id", event.source.user_id)

    line_bot_api.push_message(
        target,
        [
            TextSendMessage(text=announcement),
            ImageSendMessage(
                original_content_url=image_url,
                preview_image_url=image_url,
            ),
        ],
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
