"""저장소 접두 없는 ``#N`` 참조는 bet-lab/TMHP 에 실제로 있는 번호만 가리킨다.

2026-07-01 공개 이력을 초기화하면서 이슈 번호가 다시 1 부터 매겨졌다. 그 전에 쓴
``#165 P0``·``#166``·``#188`` 같은 옛 번호가 ``src/`` 에 남아, 없는 이슈를(나중에는
번호만 겹치는 엉뚱한 이슈를) 가리켰다. 이 시험은 네트워크 없이, 커밋된 번호 목록
``tests/data/tmhp_issue_numbers.txt`` 와 대조한다. 새 번호를 참조하면 그 번호를
GitHub 에서 확인한 뒤 목록에 더한다. 다른 저장소의 번호는 ``geolink#581`` 처럼
저장소를 붙이면 검사 대상에서 빠진다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "tests" / "data" / "tmhp_issue_numbers.txt"

# 검사하는 글: 패키지·시험·검증·스크립트 코드와 사람이 쓰는 문서.
SCAN = {
    "src": (".py",),
    "tests": (".py",),
    "validation": (".py",),
    "scripts": (".py", ".sh"),
    "docs": (".py", ".rst", ".md"),
}
# 생성물과 내려받은 라이브러리(그림 번호 '#1 hero' 같은 주석이 있다)는 뺀다.
SKIP_PARTS = {"_static", "_build", "build", "generated", "__pycache__"}

# 앞이 글자·숫자·'/'·'#'·'&'·'-' 가 아닌 '#N' 만 이슈 참조로 본다
# ('geolink#581', 'bet-lab/x#3', '&#39;', 색 '#16a34a' 는 제외된다).
REF = re.compile(r"(?<![\w/#&-])#(\d{1,4})\b")


def _allowed() -> set[int]:
    numbers: set[int] = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        numbers.update(int(tok) for tok in line.split())
    return numbers


def _scanned_files():
    for top, suffixes in SCAN.items():
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in suffixes or not path.is_file():
                continue
            if SKIP_PARTS.intersection(path.relative_to(ROOT).parts):
                continue
            yield path
    readme = ROOT / "README.md"
    if readme.is_file():
        yield readme


def test_allowlist_is_well_formed():
    numbers = _allowed()
    assert numbers, "번호 목록이 비었다"
    assert min(numbers) >= 1


def test_bare_issue_references_exist_in_tmhp():
    allowed = _allowed()
    dead = []
    for path in _scanned_files():
        if path == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in REF.finditer(line):
                if int(m.group(1)) not in allowed:
                    dead.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not dead, (
        "bet-lab/TMHP 에 없는 번호를 가리키는 '#N' 참조가 있다. 옛 이력의 번호면 지우고 뜻을 글로 "
        "적고, 다른 저장소면 'geolink#N' 처럼 저장소를 붙이고, 새로 만든 TMHP 이슈면 "
        "tests/data/tmhp_issue_numbers.txt 에 번호를 더한다:\n" + "\n".join(dead)
    )
