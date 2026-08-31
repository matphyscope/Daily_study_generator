"""
find_sections.py
-----------------
PDF의 한 챕터 범위 안에서 소단원(1.1, 1.2, ...) 제목이 시작되는 페이지를 찾아 출력하고,
전체 페이지의 절반에 가장 가까운 소단원 경계를 "자연스러운 분할 지점"으로 제안합니다.

사용법:
  python find_sections.py <pdf경로> <시작페이지> <끝페이지> <챕터번호>

예시:
  python find_sections.py source.pdf 102 150 3

주의: OCR 품질에 따라 완벽하지 않을 수 있습니다. 출력된 후보를 참고해서
config.yaml의 페이지 범위를 사람이 최종 확인/조정하는 걸 권장합니다.
"""

import re
import subprocess
import sys


def get_page_text(pdf_path: str, page: int) -> str:
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), pdf_path, "-"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def find_subsection_starts(pdf_path: str, start_page: int, end_page: int, chapter_num: int):
    """각 페이지 첫 줄들에서 'N.M' 형태의 소단원 번호가 나오는 페이지를 찾음."""
    pattern = re.compile(rf"^\s*{chapter_num}\.(\d+)\b")
    found = {}  # subsection_number -> first page seen
    for page in range(start_page, end_page + 1):
        text = get_page_text(pdf_path, page)
        for line in text.splitlines()[:6]:  # 페이지 상단 몇 줄만 확인 (소제목은 보통 상단에 위치)
            m = pattern.match(line.strip())
            if m:
                sub_num = f"{chapter_num}.{m.group(1)}"
                if sub_num not in found:
                    found[sub_num] = page
    return found


def suggest_split(found: dict, start_page: int, end_page: int):
    if not found:
        print("소단원 경계를 찾지 못했습니다. 페이지를 직접 확인해주세요.")
        return

    boundaries = sorted(found.items(), key=lambda kv: kv[1])
    total_pages = end_page - start_page + 1
    midpoint = start_page + total_pages / 2

    print(f"\n총 {total_pages}페이지 (절반 지점 ≈ {midpoint:.0f}페이지)\n")
    print("발견된 소단원:")
    for sub_num, page in boundaries:
        print(f"  {sub_num}: {page}페이지부터 시작")

    # 절반 지점에 가장 가까운 소단원 시작 페이지를 분할 지점으로 제안
    closest = min(boundaries, key=lambda kv: abs(kv[1] - midpoint))
    split_page = closest[1]

    print(f"\n제안: {closest[0]}절이 시작하는 {split_page}페이지를 기준으로 나누면 자연스럽습니다.")
    print(f"  1/2: {start_page} ~ {split_page - 1}")
    print(f"  2/2: {split_page} ~ {end_page}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)

    pdf_path, start_page, end_page, chapter_num = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    found = find_subsection_starts(pdf_path, start_page, end_page, chapter_num)
    suggest_split(found, start_page, end_page)
