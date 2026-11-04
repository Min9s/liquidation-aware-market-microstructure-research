"""Run the source checkout's tests, public demos and local documentation checks."""

from importlib import metadata, util
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, cwd, timeout=120):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, text=True,
                            capture_output=True, timeout=timeout,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(map(str, args))}")
    return result.stdout


def verify():
    missing = [name for name in ("market_microstructure", "pytest", "pyarrow")
               if util.find_spec(name) is None]
    if missing:
        print("필요한 설치가 없습니다: " + ", ".join(missing), file=sys.stderr)
        print("저장소에서 python -m pip install -e '.[dev,historical]' 실행 후 다시 확인하세요.",
              file=sys.stderr)
        return 2
    print(f"Python {sys.version.split()[0]}, pytest {metadata.version('pytest')}, "
          f"PyArrow {metadata.version('pyarrow')}", flush=True)
    with TemporaryDirectory(prefix="microstructure-verify-") as directory:
        temporary = Path(directory)
        report = temporary / "tests.xml"
        output = run([sys.executable, "-B", "-m", "pytest", "-q", "--junitxml", report], cwd=ROOT)
        suites = ElementTree.parse(report).getroot().iter("testsuite")
        totals = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
        for suite in suites:
            for name in totals:
                totals[name] += int(suite.get(name, 0))
        if not totals["tests"] or any(totals[k] for k in ("failures", "errors", "skipped")):
            print(output)
            raise RuntimeError(f"전체 테스트가 필요합니다: {totals}")
        print(f"테스트: {totals['tests']} passed, skipped=0", flush=True)
        examples = sorted((ROOT / "examples").glob("*.py"))
        if not examples:
            raise RuntimeError("No public examples found")
        for path in examples:
            output = run([sys.executable, "-B", path], cwd=temporary, timeout=30)
            if "SYNTHETIC_TEST_ONLY" not in output:
                raise RuntimeError(f"합성 입력 표기 없음: {path.name}")
            print(f"데모: {path.name} OK", flush=True)
        links = 0
        public_docs = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").rglob("*.md"))
        for path in public_docs:
            for ref in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if ref.startswith(("http://", "https://", "#")):
                    continue
                target = path.parent / ref.split("#")[0]
                if not target.is_file():
                    raise RuntimeError(f"문서 링크 없음: {path.name} -> {ref}")
                links += 1
        print(f"문서: 로컬 파일 링크 {links}개 OK (외부 URL·anchor는 검사 제외)", flush=True)
        print(run([sys.executable, "-m", "pip", "check"], cwd=temporary).strip(), flush=True)
    print("검증 완료: 테스트·공개 데모·로컬 문서 링크·설치 의존성")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(verify())
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
