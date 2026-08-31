"""
자동 공부 메일러 (Auto Study Mailer)
------------------------------------
매일 정해진 시간에 GitHub Actions가 이 스크립트를 실행합니다.

동작 순서:
1. config/config.yaml 을 읽어 구간(하루 분량 = 챕터의 절반) 목록·Drive 파일 ID 등을 확인
2. state/progress.json 을 읽어 "다음에 보낼 구간"을 확인
3. Google Drive에서 원본 PDF를 다운로드
4. 해당 구간의 페이지 범위만 텍스트로 추출
5. 분량이 많으므로 텍스트를 여러 덩어리(chunk)로 나눠 Claude API를 여러 번 호출,
   문단별 재구성 설명 + 요약 + 쉬운 설명 + 각주(Markdown 각주 문법)를 생성
   (원문을 그대로 번역하지 않고, 저작권 보호를 위해 문장을 새로 구성함)
6. 모든 chunk 결과를 하나의 Markdown 문서로 합치고, pandoc으로 .docx 로 변환
   (Markdown 각주 문법 → Word의 실제 "페이지 하단 각주"로 자동 변환됨)
7. Gmail로 나 + 지인들에게 .docx 파일을 첨부해서 발송
8. state/progress.json 을 다음 구간으로 갱신 (GitHub Actions가 커밋)

필요한 환경변수 (GitHub Secrets 로 등록):
  GOOGLE_SERVICE_ACCOUNT_JSON_B64  - Drive 접근용 서비스 계정 키(JSON)를 base64로 인코딩한 값
  ANTHROPIC_API_KEY                - Claude API 키
  GMAIL_ADDRESS                    - 보내는 사람 Gmail 주소
  GMAIL_APP_PASSWORD               - Gmail 앱 비밀번호(일반 비밀번호 아님)
  RECIPIENTS                       - 받는 사람 이메일, 쉼표(,)로 구분 (나 + 친구들)

필요한 시스템 패키지: pandoc (워크플로우에서 apt-get으로 설치)
"""

import os
import io
import re
import json
import base64
import smtplib
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import yaml
from pypdf import PdfReader

CONFIG_PATH = "config/config.yaml"
STATE_PATH = "state/progress.json"

# 청크 하나당 대략 이 정도 글자 수(원문 기준)를 넘지 않도록 나눔.
# 문단별로 자세히 풀어 쓰는 스타일이라 출력이 입력보다 훨씬 길어지므로 넉넉히 잡지 않음.
CHUNK_CHAR_LIMIT = 9000


# ---------- 1. 설정 / 상태 ----------

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"next_section_index": 0}
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


# ---------- 3. 구간 텍스트 추출 + 청크 분할 ----------

def extract_section_text(pdf_path: str, start_page: int, end_page: int) -> str:
    """start_page, end_page 는 1-based, 둘 다 포함."""
    reader = PdfReader(pdf_path)
    texts = []
    for i in range(start_page - 1, min(end_page, len(reader.pages))):
        texts.append(reader.pages[i].extract_text() or "")
    return "\n".join(texts)


def split_into_chunks(text: str, char_limit: int) -> list[str]:
    """문단(빈 줄 기준) 단위를 유지하면서 char_limit 근처에서 끊어 여러 덩어리로 나눔."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks, current = [], []
    current_len = 0
    for p in paragraphs:
        if current and current_len + len(p) > char_limit:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(p)
        current_len += len(p)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------- 4. Claude로 정리 (chunk 단위) ----------

STUDY_PROMPT_TEMPLATE = """\
당신은 물리학자·재료학자·현미경학자의 시각을 모두 가진 과학 커뮤니케이터입니다.
아래는 전공 서적의 원문 일부입니다 ({title} 중 일부, 전체 {total_chunks}개 조각 중 {chunk_index}번째).
이걸 "아침에 신문·잡지 읽듯" 가볍게 훑어도 이해가 남도록 정리해 주세요.
대상 독자는 관련 전공 대학원생입니다. 출력은 반드시 Markdown 형식으로 작성하세요.

절대 규칙 (저작권 보호를 위해 반드시 지킬 것):
- 원문을 문장 단위로 번역하지 마세요. 각 문단의 논리 전개, 비유, 설명 순서는
  최대한 살리되, 문장은 당신 자신의 표현으로 완전히 새로 구성해야 합니다.
  (원문 문장을 어순만 바꾸거나 단어만 바꾸는 것도 금지)
- 원문에서 짧은 인상적인 문장을 그대로 인용하고 싶다면 큰따옴표로 표시하고
  이 조각 안에서 최대 1회만 사용하세요.

작업 방식:
원문을 자연스러운 문단 단위로 나누고, 각 문단마다 아래 3단 구성을 반복하세요.
(소제목·수식이 나오는 문단도 동일하게 처리. 새 소단원이 시작되면 "## 소제목"으로 표시)

**[정리]**
해당 문단의 논리·비유·설명 전개 방식을 살려서, 당신의 문장으로 새로 풀어 쓴 설명.
그림(Figure)이 언급되는 문단이라면, 먼저 그 그림이 아래 중 무엇인지 판단하세요:
(A) 그래프(축이 있고 데이터 경향을 보여주는 그림) 또는 개념도(도형·화살표로 구조나
    과정을 보여주는 스케치) — 다시 그려도 되는 유형
(B) 실제 사진(현미경 사진, 실물 촬영 이미지 등) — 다시 그리면 안 되는 유형

정리 맨 앞에 굵게
"**(Fig. X.X: 이 그림이 보여주는 내용 설명. → 이 그림에서 꼭 봐야 할 포인트: ~)**"
형식으로 그림 설명을 넣으세요. 이건 (A), (B) 모두 동일합니다.

**(A) 그래프/개념도인 경우에만** 그림 설명 바로 다음 줄에 아래 형식의 Python 코드 블록을
추가해서, 원본을 그대로 베끼지 말고 같은 포인트(경향·구조·관계)를 전달하는 그림을
matplotlib으로 새로 그리세요. 축 눈금의 정확한 실측값이 필요 없다면 개략적인 형태만
살려서 단순하게 그려도 됩니다.

```figure-python
import matplotlib.pyplot as plt
# 여기에 그림을 새로 그리는 코드 작성 (한글 대신 영문 라벨 사용 권장 — 폰트 깨짐 방지)
# 마지막 줄은 반드시 아래와 같이 저장:
plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches="tight")
```

**(B) 실제 사진인 경우** 코드 블록 없이, 텍스트 설명만 조금 더 구체적으로 (촬영 대상,
특징적으로 보이는 부분 등) 작성하세요.

수식이 나오면, 수식 자체를 적고 그 아래에 등장하는 모든 변수의 이름·물리적 의미·
단위를 각주로 정리하세요 (아래 각주 규칙을 따름).

**[두 줄 요약]**
해당 문단 내용을 두 문장으로 압축.

**[쉬운 설명]**
중고등학생도 이해할 수 있는 눈높이로, 비유를 들어 쉽게 다시 설명.

표(Table)가 나오면:
- 표 안의 수치·데이터는 사실 정보이므로 그대로 사용해도 됩니다.
- 다만 원본 표의 제목 문구·구성을 그대로 베끼지 말고, 데이터를 바탕으로
  당신이 새로 표를 구성하고 제목·설명도 새로 작성하세요.

각주 규칙 (반드시 Markdown/pandoc 각주 문법을 사용 — Word에서 실제 페이지 하단 각주로 변환됨):
- 전문 용어나 기호가 처음 등장하면 본문에 `단어[^{footnote_prefix}N]` 형태로 표시하고
  (N은 이 조각 안에서 {footnote_start}번부터 순서대로 증가하는 숫자),
  해당 문단 바로 아래에 `[^{footnote_prefix}N]: 영문 용어 (한글 번역) — 설명` 줄을 추가하세요.
- 각주 식별자는 반드시 `{footnote_prefix}` 접두사를 붙이세요 (다른 조각과 번호가 겹치지 않도록).

{final_instruction}

구간 제목: {title}

원문 조각:
{content}
"""

FINAL_CHUNK_INSTRUCTION = """\
이 조각이 이 구간의 마지막 조각입니다. 모든 문단 처리가 끝난 뒤 마지막에
"## 이 구간에서 꼭 알아야 할 것" 섹션을 추가하세요. 두괄식으로: 가장 중요한 핵심
결론을 첫 문장에 먼저 제시하고, 이어서 이를 뒷받침하는 핵심 포인트 3~5개를
불릿으로 정리하세요."""


def call_claude(prompt: str, max_tokens: int = 8000) -> str:
    from anthropic import Anthropic

    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수를 자동으로 사용
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def summarize_section_to_markdown(title: str, raw_text: str) -> str:
    chunks = split_into_chunks(raw_text, CHUNK_CHAR_LIMIT)
    total = len(chunks)
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        is_last = i == total
        prompt = STUDY_PROMPT_TEMPLATE.format(
            title=title,
            total_chunks=total,
            chunk_index=i,
            footnote_prefix=f"c{i}_",
            footnote_start=1,
            final_instruction=FINAL_CHUNK_INSTRUCTION if is_last else "",
            content=chunk,
        )
        print(f"  - Claude 호출 중 ({i}/{total})...")
        parts.append(call_claude(prompt))
    return f"# {title}\n\n" + "\n\n".join(parts)


# ---------- 5. 코드 블록 → 실제 그림(PNG) 렌더링 ----------

FIGURE_CODE_PATTERN = re.compile(r"```figure-python\n(.*?)```", re.DOTALL)


def render_figures(markdown_text: str, workdir: str) -> str:
    """```figure-python``` 코드 블록을 실행해서 PNG로 저장하고, 마크다운 이미지 링크로 치환."""
    counter = 0

    def replace(match: re.Match) -> str:
        nonlocal counter
        counter += 1
        code = match.group(1)
        img_name = f"figure_{counter}.png"
        img_path = os.path.join(workdir, img_name)
        code = code.replace("OUTPUT_PATH", repr(img_path))

        script_path = os.path.join(workdir, f"_figure_{counter}.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("import matplotlib\nmatplotlib.use('Agg')\n" + code)

        try:
            subprocess.run(
                [sys.executable, script_path],
                check=True, timeout=30, capture_output=True, text=True,
            )
            return f"![]({img_name})"
        except Exception as e:
            print(f"  경고: 그림 {counter} 생성 실패, 텍스트 설명만 유지합니다. ({e})", file=sys.stderr)
            return ""

    return FIGURE_CODE_PATTERN.sub(replace, markdown_text)


# ---------- 6. Markdown → Word(.docx) 변환 ----------

def markdown_to_docx(markdown_text: str, out_path: str, workdir: str):
    md_path = os.path.join(workdir, "section.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)
    subprocess.run(
        ["pandoc", md_path, "-o", out_path, "--standalone", f"--resource-path={workdir}"],
        check=True,
    )


# ---------- 7. 이메일 발송 (Word 파일 첨부) ----------

def send_email_with_attachment(subject: str, body_text: str, attachment_path: str, recipients: list[str]):
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    with open(attachment_path, "rb") as f:
        part = MIMEBase("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    filename = os.path.basename(attachment_path)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, recipients, msg.as_string())


# ---------- 8. 메인 흐름 ----------

def main():
    config = load_config()
    state = load_state()
    sections = config["sections"]

    idx = state.get("next_section_index", 0)
    if idx >= len(sections):
        print("모든 구간을 이미 다 보냈습니다. 할 일 없음.")
        return

    section = sections[idx]
    print(f"[{idx + 1}/{len(sections)}] 구간 처리 중: {section['title']}")

    pdf_path = "source.pdf"
    download_pdf_from_drive(config["drive_file_id"], pdf_path)

    raw_text = extract_section_text(pdf_path, section["start_page"], section["end_page"])
    if not raw_text.strip():
        print("경고: 추출된 텍스트가 비어 있습니다. 페이지 범위를 확인하세요.", file=sys.stderr)

    markdown_result = summarize_section_to_markdown(section["title"], raw_text)

    workdir = "work"
    os.makedirs(workdir, exist_ok=True)
    markdown_result = render_figures(markdown_result, workdir)

    safe_name = re.sub(r"[^\w가-힣]+", "_", section["title"]).strip("_")
    docx_path = f"{safe_name}.docx"
    markdown_to_docx(markdown_result, docx_path, workdir)

    recipients = [r.strip() for r in os.environ["RECIPIENTS"].split(",") if r.strip()]
    subject = f"{config.get('subject_prefix', '오늘의 공부')} - {section['title']}"
    body_text = f"오늘의 공부 자료입니다: {section['title']}\n\n첨부된 Word 파일을 확인해주세요."
    send_email_with_attachment(subject, body_text, docx_path, recipients)

    state["next_section_index"] = idx + 1
    save_state(state)
    print("완료: 이메일 발송 및 상태 저장.")


if __name__ == "__main__":
    main()
