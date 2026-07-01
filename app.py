import os
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage,
    TextSendMessage, ImageSendMessage,
)
from poster import generate_poster

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
handler      = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
GROUP_ID     = os.environ.get("LINE_GROUP_ID", "")
BASE_URL     = os.environ.get("BASE_URL", "").rstrip("/")


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


@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()

    # Print group ID for initial setup
    if hasattr(event.source, "group_id"):
        print(f"[GROUP ID] {event.source.group_id}", flush=True)

    # Command: 業績王 姓名 [YYYY.MM.DD]  (職位從檔名自動讀取)
    match = re.match(r"業績王\s+(\S+)(?:\s+(\d{4}\.\d{2}\.\d{2}))?", text)
    if not match:
        return

    name     = match.group(1)
    date_str = match.group(2) or datetime.now().strftime("%Y.%m.%d")

    try:
        d            = datetime.strptime(date_str, "%Y.%m.%d")
        date_display = f"{d.month}/{d.day}"
        next_workday = d + timedelta(days=1)
        while next_workday.weekday() >= 5:  # 5=Sat, 6=Sun
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

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"✅ 已發送 {name} {title} 的業績王公告！"),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
