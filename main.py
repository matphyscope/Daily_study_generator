"""
자동 공부 메일러 (Auto Study Mailer)
------------------------------------
매일 정해진 시간에 GitHub Actions가 이 스크립트를 실행합니다.

동작 순서:
1. config/config.yaml 을 읽어 챕터 목록·Drive 파일 ID 등을 확인
2. state/progress.json 을 읽어 "다음에 보낼 챕터"를 확인
3. Google Drive에서 원본 PDF를 다운로드
4. 해당 챕터의 페이지 범위만 텍스트로 추출
5. Anthropic API(Claude)로 "공부하듯 상세히" 정리
6. Gmail로 나 + 지인들에게 이메일 발송
7. state/progress.json 을 다음 챕터로 갱신 (GitHub Actions가 커밋)

필요한 환경변수 (GitHub Secrets 로 등록):
  GOOGLE_SERVICE_ACCOUNT_JSON_B64  - Drive 접근용 서비스 계정 키(JSON)를 base64로 인코딩한 값
  ANTHROPIC_API_KEY                - Claude API 키
  GMAIL_ADDRESS                    - 보내는 사람 Gmail 주소
  GMAIL_APP_PASSWORD               - Gmail 앱 비밀번호(일반 비밀번호 아님)
  RECIPIENTS                       - 받는 사람 이메일, 쉼표(,)로 구분 (나 + 친구들)
"""

import os
import io
import json
import base64
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml
from pypdf import PdfReader

CONFIG_PATH = "config/config.yaml"
STATE_PATH = "state/progress.json"


# ---------- 1. 설정 / 상태 ----------

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"next_chapter_index": 0}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- 2. Google Drive에서 PDF 다운로드 ----------

def download_pdf_from_drive(file_id: str, dest_path: str):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from google.oauth2 import service_account

    creds_b64 = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"]
    creds_json = json.loads(base64.b64decode(creds_b64))
    creds = service_account.Credentials.from_service_account_info(
        creds_json, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    service = build("drive", "v3", credentials=creds)

    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(dest_path, "wb")
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.close()


# ---------- 3. 챕터 텍스트 추출 ----------

def extract_chapter_text(pdf_path: str, start_page: int, end_page: int) -> str:
    """start_page, end_page 는 1-based, 둘 다 포함."""
    reader = PdfReader(pdf_path)
    texts = []
    for i in range(start_page - 1, min(end_page, len(reader.pages))):
        texts.append(reader.pages[i].extract_text() or "")
    return "\n".join(texts)


# ---------- 4. Claude로 정리 ----------

STUDY_PROMPT_TEMPLATE = """\
아래는 책의 한 챕터 원문입니다. 이 내용을 공부하듯이 상세하게 정리해 주세요.

정리 형식:
1. 챕터 핵심 개념 설명 (배경과 원리까지 풀어서 설명)
2. 중요 용어 / 핵심 문장 정리
3. 한눈에 보는 요약 (불릿 정리)

챕터 제목: {title}

원문:
{content}
"""


def summarize_chapter(title: str, content: str) -> str:
    from anthropic import Anthropic

    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수를 자동으로 사용
    prompt = STUDY_PROMPT_TEMPLATE.format(title=title, content=content[:20000])

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


# ---------- 5. 이메일 발송 ----------

def send_email(subject: str, body_markdown: str, recipients: list[str]):
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_markdown, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, recipients, msg.as_string())


# ---------- 6. 메인 흐름 ----------

def main():
    config = load_config()
    state = load_state()
    chapters = config["chapters"]

    idx = state.get("next_chapter_index", 0)
    if idx >= len(chapters):
        print("모든 챕터를 이미 다 보냈습니다. 할 일 없음.")
        return

    chapter = chapters[idx]
    print(f"[{idx + 1}/{len(chapters)}] 챕터 처리 중: {chapter['title']}")

    pdf_path = "source.pdf"
    download_pdf_from_drive(config["drive_file_id"], pdf_path)

    raw_text = extract_chapter_text(pdf_path, chapter["start_page"], chapter["end_page"])
    if not raw_text.strip():
        print("경고: 추출된 텍스트가 비어 있습니다. 페이지 범위를 확인하세요.", file=sys.stderr)

    summary = summarize_chapter(chapter["title"], raw_text)

    recipients = [r.strip() for r in os.environ["RECIPIENTS"].split(",") if r.strip()]
    subject = f"{config.get('subject_prefix', '오늘의 공부')} - {chapter['title']}"
    send_email(subject, summary, recipients)

    state["next_chapter_index"] = idx + 1
    save_state(state)
    print("완료: 이메일 발송 및 상태 저장.")


if __name__ == "__main__":
    main()
