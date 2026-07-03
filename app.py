import os
import re
import json
import base64
import anthropic
from datetime import datetime, timedelta
from flask import Flask, request, abort, send_from_directory
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, ImageMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from poster import generate_poster

app = Flask(__name__)

# Bot A：業績統計群組
handler_a       = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_A"])
configuration_a = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN_A"])

# Bot B：業績王公告群組
handler_b       = WebhookHandler(os.environ["LINE_CHANNEL_SECRET_B"])
configuration_b = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN_B"])
GROUP_ID_B      = os.environ.get("LINE_GROUP_ID_B", "")

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

WINNER_SUBSTITUTES = {
    "方駿威": "林倢伃",
    "王馨婕": "邱耀欽",
}

# Bot A state: group_id -> 最新統計表文字
state: dict[str, str] = {}

# Bot B state: 待結算的報表圖片（base64）
pending_reports: list[str] = []


# ── Helpers ────────────────────────────────────────────────────────────────

def fmt(n: float) -> str:
    n = round(n, 1)
    return str(int(n)) if n == int(n) else str(n)


def is_stats_table(text: str) -> bool:
    return "目標" in text and "累積：" in text and "尚差：" in text


def add_medical_note(text: str, name: str, amount: float) -> tuple[str | None, str | None]:
    lines = text.split("\n")
    result, found = [], False
    for line in lines:
        if re.match(rf"^{re.escape(name)}[：:]", line):
            found = True
            result.append(line + f"（體檢件：{fmt(amount)}C）")
        else:
            result.append(line)
    if not found:
        return None, f"找不到「{name}」，請確認名字是否正確"
    return "\n".join(result), None


def remove_medical_note(text: str, name: str, amount: float) -> tuple[str | None, str | None]:
    lines = text.split("\n")
    result, found = [], False
    for line in lines:
        if re.match(rf"^{re.escape(name)}[：:]", line):
            found = True
            note = f"（體檢件：{fmt(amount)}C）"
            if note not in line:
                return None, f"找不到「{name}」的體檢件 {fmt(amount)}C 備註"
            result.append(line.replace(note, ""))
        else:
            result.append(line)
    if not found:
        return None, f"找不到「{name}」，請確認名字是否正確"
    return "\n".join(result), None


def update_table(text: str, name: str, amount: float) -> tuple[str | None, str | None]:
    lines = text.split("\n")
    result, found = [], False
    for line in lines:
        if re.match(rf"^{re.escape(name)}[：:]", line):
            found = True
            m = re.match(rf"^({re.escape(name)}[：:])([\d.]+)([Cc]?)(.*)", line)
            if m:
                new_val = round(float(m.group(2)) + amount, 1)
                suffix = m.group(3) or "c"
                result.append(f"{m.group(1)}{fmt(new_val)}{suffix}{m.group(4)}")
            else:
                sep = "：" if "：" in line else ":"
                result.append(f"{name}{sep}{fmt(amount)}c")
        elif re.match(r"^累積[：:]", line):
            m = re.match(r"^(累積[：:])([\d.]+)(.*)", line)
            if m:
                result.append(f"{m.group(1)}{fmt(round(float(m.group(2)) + amount, 1))}{m.group(3)}")
            else:
                result.append(line)
        elif re.match(r"^尚差[：:]", line):
            m = re.match(r"^(尚差[：:])([\d.]+)(.*)", line)
            if m:
                result.append(f"{m.group(1)}{fmt(round(float(m.group(2)) - amount, 1))}{m.group(3)}")
            else:
                result.append(line)
        else:
            result.append(line)
    if not found:
        return None, f"找不到「{name}」，請確認名字是否正確"
    return "\n".join(result), None


def send_reply(configuration: Configuration, reply_token: str, text: str):
    with ApiClient(configuration) as api:
        MessagingApi(api).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
        )


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/health")
def health():
    return "OK"


@app.route("/static/posters/<filename>")
def serve_poster(filename):
    return send_from_directory("static/posters", filename)


# ── Bot A：業績統計 ──────────────────────────────────────────────────────────

@app.route("/callback_a", methods=["POST"])
def callback_a():
    sig = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler_a.handle(body, sig)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler_a.add(MessageEvent, message=TextMessageContent)
def on_message_a(event):
    text = event.message.text.strip()
    src = event.source
    gid = getattr(src, "group_id", None) or getattr(src, "room_id", None)
    if not gid:
        return

    def rep(msg): send_reply(configuration_a, event.reply_token, msg)

    if text == "群組ID":
        rep(f"Group ID: {gid}")
        return

    if is_stats_table(text):
        state[gid] = text
        rep("✅ 統計表已記錄，Bot 待命中")
        return

    m = re.match(r"^(.+?)\s*收\s*.+?([\d.]+)\s*[Cc].*體檢件", text)
    if m:
        name, amount = m.group(1).strip(), float(m.group(2))
        if gid not in state: rep("⚠️ 尚未初始化，請先貼統計表"); return
        new_text, err = add_medical_note(state[gid], name, amount)
        if err: rep(f"⚠️ {err}"); return
        state[gid] = new_text; rep(new_text); return

    m = re.match(r"^(.+?)\s*收\s*.+?([\d.]+)\s*[Cc]\s*$", text)
    if m:
        name, amount = m.group(1).strip(), float(m.group(2))
        if gid not in state: rep("⚠️ 尚未初始化，請先貼統計表"); return
        new_text, err = update_table(state[gid], name, amount)
        if err: rep(f"⚠️ {err}"); return
        state[gid] = new_text; rep(new_text); return

    m = re.match(r"^(.+?)\s*退\s*([\d.]+)\s*[Cc]\s*$", text)
    if m:
        name, amount = m.group(1).strip(), float(m.group(2))
        if gid not in state: rep("⚠️ 尚未初始化，請先貼統計表"); return
        new_text, err = update_table(state[gid], name, -amount)
        if err: rep(f"⚠️ {err}"); return
        state[gid] = new_text; rep(new_text); return

    m = re.match(r"^(.+?)\s*體檢取消\s*([\d.]+)\s*[Cc]\s*$", text)
    if m:
        name, amount = m.group(1).strip(), float(m.group(2))
        if gid not in state: rep("⚠️ 尚未初始化，請先貼統計表"); return
        new_text, err = remove_medical_note(state[gid], name, amount)
        if err: rep(f"⚠️ {err}"); return
        state[gid] = new_text; rep(new_text); return

    m = re.match(r"^(.+?)\s*體檢通過\s*([\d.]+)\s*[Cc]\s*$", text)
    if m:
        name, amount = m.group(1).strip(), float(m.group(2))
        if gid not in state: rep("⚠️ 尚未初始化，請先貼統計表"); return
        text1, err = remove_medical_note(state[gid], name, amount)
        if err: rep(f"⚠️ {err}"); return
        new_text, err = update_table(text1, name, amount)
        if err: rep(f"⚠️ {err}"); return
        state[gid] = new_text; rep(new_text)


# ── Bot B：業績王公告 ──────────────────────────────────────────────────────────

@app.route("/callback_b", methods=["POST"])
def callback_b():
    sig = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler_b.handle(body, sig)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler_b.add(MessageEvent, message=ImageMessageContent)
def on_image_b(event):
    with ApiClient(configuration_b) as api:
        content = MessagingApiBlob(api).get_message_content(event.message.id)
    pending_reports.append(base64.b64encode(content).decode())


@handler_b.add(MessageEvent, message=TextMessageContent)
def on_message_b(event):
    text = event.message.text.strip()
    src = event.source
    gid = getattr(src, "group_id", None) or getattr(src, "room_id", None)

    def rep(msg): send_reply(configuration_b, event.reply_token, msg)

    if gid:
        print(f"[GROUP ID] {gid}", flush=True)

    if text == "群組ID":
        rep(f"Group ID: {gid}")
        return

    if text == "結算":
        if not pending_reports:
            rep("❌ 尚未收到任何報表，請先傳報表照片")
            return

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        content = []
        for img_b64 in pending_reports:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
            })

        members = []
        for fname in os.listdir("photos"):
            stem, ext = os.path.splitext(fname)
            if ext.lower() in (".jpg", ".jpeg", ".png") and stem:
                name_only = re.match(r'[\u4e00-\u9fff]+', stem)
                if name_only:
                    members.append(name_only.group(0))

        content.append({
            "type": "text",
            "text": (
                f"這些是三商美邦業績受理日報表。"
                f"每位業務員底下有「業務員小計」區塊，可能有多行（TWD、USD 等幣別分開列），"
                f"最後一行（無幣別標示）是該業務員所有幣別換算台幣後的台幣FYC總計（最右欄數字）。"
                f"注意：「通訊處合計」是整個部門的加總，不是個人數字，請忽略。"
                f"請列出每位業務員姓名與其台幣FYC合計，以純 JSON 格式回傳，例如：{{\"古銘森\": 23284, \"李采穎\": 56566}}"
                f"姓名請從以下名單中選出最符合的：{'、'.join(members)}。"
                f"只輸出 JSON，不要其他文字。"
            ),
        })

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": content}],
            )
            raw = response.content[0].text.strip()
            start = raw.find('{')
            if start == -1:
                raise ValueError(f"無法解析回傳內容：{raw}")
            fyc_map, _ = json.JSONDecoder().raw_decode(raw[start:])
            winner_name = max(fyc_map, key=fyc_map.get)
            winner_name = WINNER_SUBSTITUTES.get(winner_name, winner_name)
            print(f"[FYC結果] {fyc_map} → 業績王：{winner_name}", flush=True)
        except Exception as e:
            rep(f"❌ 讀取報表失敗：{e}")
            return
        finally:
            pending_reports.clear()

        try:
            _announce(event, winner_name)
        except Exception as e:
            print(f"[_announce error] {e}", flush=True)
            rep(f"❌ 發布業績王失敗：{e}")
        return

    m = re.match(r"#業績王\s+(\S+)(?:\s+(\d{4}\.\d{2}\.\d{2}))?", text)
    if m:
        _announce(event, m.group(1), m.group(2))


def _announce(event, name: str, date_str: str = None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y.%m.%d")

    try:
        d = datetime.strptime(date_str, "%Y.%m.%d")
        date_display = f"{d.month}/{d.day}"
        next_workday = d + timedelta(days=1)
        while next_workday.weekday() >= 5:
            next_workday += timedelta(days=1)
        next_day = f"{next_workday.month}/{next_workday.day}"
    except Exception:
        date_display, next_day = date_str, "下一個工作日"

    poster_path, title = generate_poster(name, date_str=date_str)
    if poster_path is None:
        send_reply(configuration_b, event.reply_token,
                   f"❌ 找不到 {name} 的照片\n請將照片命名為「{name}職位.jpg」\n例：{name}業務主任.jpg")
        return

    filename = os.path.basename(poster_path)
    image_url = f"{BASE_URL}/static/posters/{filename}"
    announcement = (
        f"恭喜{date_display}業績王\n"
        f"「{name} {title}」\n"
        f"請 {name} 於{next_day}晨會上台分享唷～\n"
        f"🇫🇷迎戰北法🇫🇷\n"
        f"Go!!Go!!Go🎉🎉🎉"
    )

    src = event.source
    target = GROUP_ID_B or getattr(src, "group_id", None) or src.user_id
    with ApiClient(configuration_b) as api:
        MessagingApi(api).push_message(
            PushMessageRequest(
                to=target,
                messages=[
                    TextMessage(text=announcement),
                    ImageMessage(original_content_url=image_url, preview_image_url=image_url),
                ],
            )
        )
    send_reply(configuration_b, event.reply_token, f"✅ 已發送 {name} {title} 的業績王公告！")


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5000)))
