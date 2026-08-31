# 자동 공부 메일러 (Auto Study Mailer)

매일 아침, Google Drive에 올려둔 PDF를 챕터의 절반씩 읽어서 Claude가 문단마다
"정리·요약·쉬운설명"으로 풀어 쓴 뒤, **Word(.docx) 파일**로 만들어
나와 지정한 사람들에게 이메일로 자동 발송합니다. GitHub Actions(클라우드)에서 실행되므로
**내 컴퓨터가 꺼져 있어도 매일 자동으로 동작**합니다.

---

## 준비물 체크리스트

- [ ] GitHub 계정 (무료)
- [ ] Google 계정 + Drive에 올린 PDF 파일
- [ ] Google Cloud 서비스 계정 (Drive 파일 읽기용, 무료)
- [ ] Gmail 계정 + 앱 비밀번호
- [ ] Anthropic API 키 (console.anthropic.com 에서 발급, 사용한 만큼 과금)

---

## 1단계. GitHub 저장소 만들기

1. GitHub에서 새 저장소(New repository)를 만듭니다. (Private 추천 — 이메일 목록 등이 들어가므로)
2. 이 프로젝트 폴더의 파일들을 그대로 업로드하거나 `git push` 합니다.

## 2단계. Google Drive 서비스 계정 만들기 (PDF 읽기용)

1. https://console.cloud.google.com 에서 새 프로젝트 생성
2. "API 및 서비스" → "라이브러리"에서 **Google Drive API** 활성화
3. "API 및 서비스" → "사용자 인증 정보" → "사용자 인증 정보 만들기" → **서비스 계정** 생성
4. 생성된 서비스 계정의 "키" 탭에서 **JSON 키 생성** → 파일이 다운로드됨
5. 다운로드된 JSON 파일 안의 `client_email` 값(예: `xxx@xxx.iam.gserviceaccount.com`)을 복사
6. Google Drive에서 PDF가 들어있는 **폴더(또는 파일)를 이 이메일 주소와 공유** (뷰어 권한이면 충분)
7. PDF 파일을 열었을 때 주소창의 `https://drive.google.com/file/d/{이 부분}/view` 값을 복사해서
   `config/config.yaml`의 `drive_file_id`에 넣기

## 3단계. Gmail 앱 비밀번호 만들기

1. Google 계정에서 **2단계 인증**을 켭니다 (앱 비밀번호는 2단계 인증 켜야 생성 가능)
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호 생성
3. 생성된 16자리 비밀번호를 복사해둡니다 (일반 로그인 비밀번호와 다름)

## 4단계. Anthropic API 키 발급

1. https://console.anthropic.com 에서 로그인 후 API 키 생성

## 5단계. GitHub 저장소에 Secrets 등록

저장소 → Settings → Secrets and variables → Actions → "New repository secret" 에서 아래 5개를 등록:

| Secret 이름 | 값 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | 2단계에서 받은 JSON 파일 전체 내용을 base64로 인코딩한 값 (아래 명령 참고) |
| `ANTHROPIC_API_KEY` | 4단계에서 받은 키 |
| `GMAIL_ADDRESS` | 보내는 사람 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | 3단계에서 받은 16자리 앱 비밀번호 |
| `RECIPIENTS` | 받는 사람 이메일 목록, 쉼표로 구분. 예: `me@gmail.com,friend1@gmail.com,friend2@naver.com` |

JSON 파일을 base64로 인코딩하는 방법 (터미널에서):
```bash
# Mac/Linux
base64 -i service-account.json | tr -d '\n'

# Windows (PowerShell)
[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))
```
출력된 긴 문자열을 그대로 `GOOGLE_SERVICE_ACCOUNT_JSON_B64` 값으로 등록하세요.

## 6단계. 하루 분량(구간) 설정하기

`config/config.yaml` 파일을 열어서, PDF의 실제 소단원 제목과 페이지 범위를 채워 넣습니다.
(자동으로 구간을 인식하지 않고 직접 지정하는 방식이라 어떤 PDF 형식이든 안전하게 동작해요.)

문단별로 자세히 풀어 쓰는 정리 스타일이라, 챕터 전체를 하루 분량으로 넣으면
Word 파일이 너무 길어져요. **챕터의 절반** 정도를 하루 분량으로 나누는 걸 추천해요.

## 7단계. 테스트 실행

저장소의 "Actions" 탭 → "Daily Study Mailer" → "Run workflow" 버튼으로 수동 실행해서
이메일이 잘 오는지 먼저 확인해보세요. 정상 작동하면 이후로는 매일 한국시간 오전 7시에 자동 실행됩니다.

---

## 정리 스타일

- 원문을 문장 단위로 번역하지 않고, 문단의 논리·비유·전개 방식만 살려서 새로운 문장으로 재구성합니다 (저작권 보호).
- 문단마다 [정리] → [두 줄 요약] → [쉬운 설명] 3단 구성이 반복됩니다.
- **그림 처리 (종류에 따라 다르게 처리됩니다)**
  - **그래프/개념도** (축이 있는 그래프, 도형·화살표로 구조를 보여주는 스케치): 원본을 그대로
    베끼지 않고, 같은 포인트를 전달하는 그림을 matplotlib 코드로 새로 그려서
    **실제 이미지로 Word 문서에 삽입**됩니다.
  - **실제 사진** (현미경 사진, 실물 촬영 이미지): 다시 그리지 않고, "(Fig. X.X: ~ / →
    꼭 봐야 할 포인트: ~)" 형태의 구체적인 텍스트 설명으로 대체됩니다.
- 수식이 나오면 모든 변수의 이름·의미·단위를 각주로 정리합니다.
- 전문 용어는 Markdown 각주 문법(`[^n]`)으로 표시되고, pandoc이 Word로 변환할 때
  **실제 페이지 하단 각주**로 자동 변환됩니다.
- 구간이 끝나면 마지막에 "이 구간에서 꼭 알아야 할 것"을 두괄식으로 정리합니다.
- 분량이 많은 구간(챕터 절반)은 여러 조각으로 나눠 Claude를 여러 번 호출한 뒤 하나의
  Word 문서로 합칩니다. 각주 번호는 조각마다 겹치지 않도록 자동으로 구분됩니다.

## 동작 원리 요약

- `state/progress.json`에 "다음에 보낼 구간 번호"가 저장되어 있고, 실행할 때마다 자동으로 1씩 증가합니다.
- 모든 구간을 다 보내면 더 이상 이메일이 발송되지 않습니다 (콘솔에 안내 메시지만 출력).
- 다시 처음부터 보내고 싶다면 `state/progress.json`의 `next_section_index`를 0으로 바꾸면 됩니다.
- 기본 일정은 **한국시간 기준 월~금 오전 7시** (주말 제외)입니다. 바꾸고 싶다면
  `.github/workflows/daily-study.yml`의 cron 값을 수정하세요 (UTC 기준이라, 한국시간 - 9시간으로 계산).
- Markdown → Word 변환에는 `pandoc`이 필요하며, GitHub Actions 워크플로우가 실행 때마다
  자동으로 설치합니다 (별도 설정 불필요).
