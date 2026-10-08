# Unlimited OCR 최적화 보고서

작성일: 2026-10-08. 적용 버전: KPaper 0.3.5, macOS 빌드 12.

비교 기준은 [0.3.4 커밋 ea7c99d](https://github.com/ColdTbrew/KPaper/commit/ea7c99d)이고, 최적화 구현은 [7986613](https://github.com/ColdTbrew/KPaper/commit/7986613)에 포함됩니다. 구현은 이미 `main`과 [0.3.5 릴리스](https://github.com/ColdTbrew/KPaper/releases/tag/v0.3.5)에 반영됐습니다. 후속 검토 PR은 0.3.4 기준 브랜치와 비교해 실제 구현 변경도 보여줍니다.

## 결과

동일 로컬 PDF 3페이지에서 MLX 최대 메모리가 5.79GB에서 4.40GB로 약 24% 감소했습니다. 처리 시간은 52.7초에서 46.1~50.1초로 줄었으며, 유효한 페이지 캐시가 있는 재실행은 약 0.56초로 끝났습니다. 기본 모델의 가중치와 입력 해상도를 유지했고 디지털 PDF의 native 본문 58개 블록이 동일한 것을 확인했습니다.

수치는 단일 문서의 측정입니다. 전체 프로세스 메모리 절감률, 장시간 실행의 누수 해소, 모든 PDF의 OCR 정확도 향상을 입증하는 결과는 아닙니다.

## 문제와 원인

0.3.4 코드에서는 모델을 로드한 상태로 PDF 전체 스크린샷 목록을 만들고 보관했습니다. 비전 인코더는 한 페이지의 여러 crop을 묶어 계산하며 큰 중간 행렬을 만들었고, 명확한 텍스트 페이지에도 모델을 실행했습니다. 여러 가져오기 작업이 동시에 OCR 모델을 로드하는 것을 제한하지 않았으며, 사용이 끝난 MLX 재사용 캐시를 명시적으로 비우지 않았습니다.

KPaper 0.3.5는 기본 Unlimited-OCR MXFP8 모델을 유지하면서 PDF 렌더링, 비전 연산의 일시 메모리와 모델 수명을 제한합니다. Python 인터프리터를 제거하는 것만으로 모델 가중치와 Metal 연산 메모리가 없어지지는 않습니다. 현재 추론은 MLX/Metal에서, PDF 래스터 생성은 PyMuPDF의 네이티브 코드에서 수행합니다.

## 적용한 변경

- 전체 스크린샷 목록 대신 한 페이지씩 렌더링합니다. 저장된 페이지 PNG는 원본 그림·표·수식 crop에 계속 사용합니다.
- 비전 인코더의 여러 crop을 순서대로 계산하고 평가합니다. 입력 해상도, crop 순서, 가중치는 유지합니다.
- SAM 전역 attention은 전체 key/value를 유지한 채 query를 256개씩 계산합니다. 큰 상대 위치 행렬을 한 번에 생성하지 않습니다. 원래 upstream attention과 계산 결과의 일치 여부를 테스트합니다.
- MLX allocator 재사용 캐시를 256MiB로 제한합니다. PDF가 끝나거나 실패하면 모델·프로세서·Metal 캐시를 명시적으로 해제합니다. 이 캐시 한도는 전체 프로세스 메모리의 상한이 아닙니다.
- 사용자별 파일 잠금으로 여러 KPaper 프로세스의 OCR 모델 중복 로드를 막습니다. 기다리는 작업은 진행 상태에 대기를 표시하며, 모델이 필요한 시점에만 잠금을 잡습니다.
- 스캔 또는 그림·벡터 도형·수식의 징후가 있는 페이지에는 grounded 분석을 유지합니다. 명확한 일반 텍스트 페이지는 native 추출을 사용합니다.
- 페이지 이미지, 모델의 로컬 HF revision/파일 정보, 최대 출력 토큰과 처리 버전별로 완료된 grounded 결과를 캐시합니다. 모델 로드 전에 캐시를 확인하며, 잘린 응답은 저장하지 않습니다.

| 변경 위치 | 책임 | 유지하는 조건 |
| --- | --- | --- |
| `scripts/kpaper.py` | 페이지 단위 렌더링, 텍스트 페이지 분기, 종료·오류 시 모델 해제 | 페이지 수 제한, 원본 crop, 디지털 본문 추출 |
| `scripts/pdf_layout.py` | 지연 모델 로드, 캐시 판정, MLX 캐시 관리 | 모델·이미지 변경 시 새 분석, 미완료 결과 저장 금지 |
| `scripts/ocr_runtime.py` | 사용자별 프로세스 잠금, crop 직렬화, SAM query 분할 | 같은 가중치와 crop 순서, 전체 key/value 범위 |
| `tests/test_ocr_runtime.py` | 계산 결과와 실패·재사용 경로 검증 | 12개 OCR 회귀 사례 |

SAM query 분할은 softmax를 계산하는 key 축을 잘라내지 않습니다. 4096개 query를 256개씩 계산하므로 큰 attention bias의 query 차원이 1/16로 줄어듭니다. 이는 해당 중간 행렬에 대한 설명이며 전체 메모리가 1/16이 된다는 뜻은 아닙니다.

## 측정 범위

2026-10-08, Apple M4 24GB, `kaldi-2011.pdf` 첫 3페이지. 기본 `sahilchachra/unlimited-ocr-mxfp8-mlx`, 144dpi, 최대 8192토큰, 모델 파일은 이미 내려받은 상태입니다. 번역 API는 호출하지 않았습니다. 다운로드 시간은 포함하지 않습니다.

실행 환경은 Python 3.14.3, MLX 0.32.0, mlx-vlm 0.6.4, PyMuPDF 1.28.0, LiteParse 2.0.1, pdf-inspector 1.14.2입니다. 원시 수치와 입력 PDF의 SHA-256은 [측정 기록 JSON](benchmarks/ocr-2026-10-08.json)에 보관합니다. 논문 본문·PDF·이미지·개인 경로·인증 정보는 기록에 포함하지 않습니다. 시간은 `pdf_to_source_html()` 안에서 측정한 monotonic 경과 시간이며 바깥 `uv` 프로세스의 시작 시간은 제외합니다. GB는 10^9바이트입니다.

| 항목 | 0.3.4 | 0.3.5 |
| --- | ---: | ---: |
| PDF 가져오기 시간 | 52.7초 | 46.1~50.1초 (2회) |
| MLX 최대 할당량 | 5.79GB | 4.40GB |
| 분석 후 MLX 재사용 캐시 | 3.98GB | 0GB |
| 텍스트 블록 (제목 포함) | 59 | 59 |
| 재처리 시간 (유효한 캐시 있음) | 없음 | 약 0.5초, 모델 로드 없음 |

단계별 실측은 다음과 같습니다. 모델·해상도 설정은 같고, 첫 처리에서는 완료된 페이지 캐시를 사용하지 않았습니다.

| 측정 단계 | 시간 | MLX 최대 할당량 | 분석 후 MLX 캐시 | 모델 생성 / 캐시 사용 페이지 |
| --- | ---: | ---: | ---: | --- |
| 0.3.4 기준 | 52.6907초 | 5.79198GB | 3.97953GB | 3 / 0 |
| 페이지 처리·crop 직렬화·캐시 한도 변경 | 47.0708초 | 5.33731GB | 0GB | 3 / 0 |
| SAM query 분할 포함, 1회차 | 50.0582초 | 4.40152GB | 0GB | 3 / 0 |
| SAM query 분할 포함, 2회차 | 46.1286초 | 4.40152GB | 0GB | 3 / 0 |
| 완료된 페이지 캐시 재사용 | 0.5589초 | 0GB | 0GB | 0 / 3 |

기준 실행은 1회, 최종 신규 분석은 2회입니다. 단계별 변경을 묶어서 측정했으므로 각 최적화의 독립 효과나 통계적 유의성을 계산하지 않았습니다. 0.3.4 실행의 생성 횟수는 코드 경로와 진행 로그로 확인했으며 당시 버전에는 `ocr_runtime` 집계 필드가 없었습니다.

MLX 최고치는 약 24% 감소했습니다. 이는 단일 로컬 PDF의 측정이며, Activity Monitor의 전체 프로세스 메모리나 모든 문서의 절감률을 뜻하지 않습니다. 모델 자체의 가중치 메모리는 계속 필요합니다. 재실행에서는 유효한 캐시를 재사용해 모델을 로드하지 않습니다. 동일 해상도라도 렌더러·부동소수점 연산에 따라 grounded box와 모델 출력이 조금 달라질 수 있습니다. 본문은 디지털 PDF의 native 텍스트를 유지하며, 원본 시각 자료 crop도 확인합니다.

## 재현 절차

동일한 입력 PDF를 준비하고, 새 paper ID 또는 새 assets 디렉터리로 페이지 캐시가 없는 실행을 먼저 측정합니다. 모델 다운로드는 사전에 완료해 둡니다. 다음 명령은 번역 API를 호출하지 않습니다.

```bash
./kpaper pdf-import \
  --paper-id ocr-measurement \
  --pdf /path/to/kaldi-2011.pdf \
  --layout-backend auto \
  --layout-model sahilchachra/unlimited-ocr-mxfp8-mlx \
  --image-dpi 144 --max-pages 3 --layout-max-tokens 8192 \
  --json
```

같은 명령을 다시 실행하면 `cache_hits: 3`, `generated_pages: 0`으로 완료된 페이지 재사용을 확인할 수 있습니다. 최초 분석과 캐시 재처리는 분리해 기록해야 합니다. 시간까지 같은 범위로 측정하려면 다음처럼 함수 호출 안에서 잽니다.

```python
import sys
import time
from pathlib import Path
sys.path.insert(0, "scripts")
import kpaper

started = time.monotonic()
result = kpaper.pdf_to_source_html(
    Path("/path/to/kaldi-2011.pdf"),
    Path("inputs/ocr-measurement.source.html"),
    Path("inputs/assets/ocr-measurement"),
    "ocr-measurement", "Kaldi", 144, 3, "auto",
    kpaper.pdf_layout.DEFAULT_LAYOUT_MODEL, 8192, False,
)
print({"seconds": time.monotonic() - started, "ocr_runtime": result["ocr_runtime"]})
```

0.3.4와 비교할 때는 각각의 체크아웃에서 같은 PDF와 별도 출력·assets 디렉터리를 사용합니다. 그 버전에는 `ocr_runtime` 필드가 없으므로 `mlx.core.get_peak_memory()`와 `get_cache_memory()`를 호출 후 읽습니다. 이 보고서의 원본 모델 파일은 캐시에 있는 상태였고, Metal 컴파일·OS 파일 캐시까지 완전히 비운 실험은 아닙니다.

`ocr_runtime.peak_mlx_bytes`는 모델의 MLX 할당 최고치이고, `generated_pages`와 `cache_hits`는 이번 실행에서 생성 또는 재사용한 페이지 수입니다. `native_only_pages`는 모델이 필요 없는 일반 텍스트 페이지입니다. 캐시를 강제로 새로 만들려면 해당 논문의 `inputs/assets/<paper-id>/layout/cache/`만 제거하면 됩니다. `--force`는 PDF 다운로드 덮어쓰기 옵션이며 OCR 캐시 삭제 옵션이 아닙니다.

## 검증과 남은 범위

- Python 문법 검사와 전체 회귀 테스트 95개가 통과했습니다. 최종 프로세스 잠금 변경 후에는 OCR 테스트 12개를 다시 실행해 통과했습니다.
- 실제 MLX 연산에서 분할 SAM attention과 upstream 전체 attention을 `rtol=1e-5`, `atol=1e-5`로 비교했습니다. crop 순서와 전달되는 patch embedding의 값도 확인했습니다.
- 캐시 사용 시 모델 로드를 건너뛰고, 이미지·모델 변경 또는 토큰 한도에 도달한 응답을 재사용하지 않는 경로를 확인했습니다.
- 일반 텍스트는 모델을 생략하고 벡터 도형·수식은 분석을 유지합니다. 실패 시 모델 해제, 디지털 native fallback, 빈 페이지 처리, 원문 텍스트가 없는 스캔의 명시적 실패를 확인했습니다.
- 디지털 PDF 본문 58개 블록이 변경 전후 동일합니다. 제목을 포함한 텍스트 블록 집계는 양쪽 모두 59개입니다.
- 한 페이지 스캔형 PDF에서 본문 10개 블록·완전한 시각 자료 crop 1개와 `FEATURE EXTRACTION`, `ACOUSTIC MODELING` 절 제목을 확인했습니다.
- 실제 브라우저에서 결과의 그림과 표가 로드되는 것을 확인했습니다. 앱 빌드, Info.plist, 서명, DMG·ZIP 체크섬을 검증했고 실행 앱의 0.3.5 (12)를 확인했습니다.

PDF renderer가 LiteParse 스크린샷에서 PyMuPDF로 바뀌었고 연산 평가 순서도 달라졌으므로, OCR 출력과 bbox의 완전한 동일성을 보장하지 않습니다. 비교 문서에서는 이전에 잡히지 않은 원본 그림 1개가 추가로 검출됐습니다. 디지털 본문은 native 텍스트를 유지하지만, 스캔 정확도는 별도 CER/WER 데이터셋으로 평가하지 않았습니다.

추가 검증 대상으로는 장문·혼합 PDF, 세밀한 수식과 다단 표, 16GB 장치의 장시간 메모리 추이, 병렬 문서의 대기 시간, 더 큰 스캔 데이터셋이 남아 있습니다. 256MiB는 allocator 재사용 캐시 한도이며 전체 메모리 상한이 아닙니다. 문서 캐시는 디스크 공간을 사용하며 자동 용량 정리는 이번 구현에 포함되지 않습니다.

## 조사한 대안

| 방식 | 장점 | 현재 판단 |
| --- | --- | --- |
| 기존 MLX의 메모리·작업 수명 최적화 | 같은 모델과 전체 시각 정보 유지, 즉시 검증 가능 | 기본 경로에 적용 |
| Swift MLX로 OCR 이전 | Python 런타임 없이 네이티브 앱에 통합 가능 | Unlimited/DeepSeek OCR 지원 PR이 아직 Open이므로 현재 배포 경로에는 사용하지 않음 |
| 4bit MLX 모델 | 가중치 크기를 더 줄일 수 있음 | 모델 배포자의 FUNSD 결과는 MXFP8보다 문자 오류가 높음. 품질 검증 없이 기본 모델을 교체하지 않음 |
| GGUF/llama.cpp | C/C++ 추론, 여러 플랫폼 지원 가능 | Unlimited 전용 sliding-cache·processor의 실제 지원과 같은 PDF의 품질·메모리 검증이 추가로 필요 |
| PyMuPDF native만 사용 | VLM 없이 빠르고 작은 메모리 사용 | 일반 텍스트에는 적용. 복합 벡터 그림·수식·스캔 전체에 강제하면 구조나 텍스트가 손실될 수 있어 선택 옵션으로 유지 |

참고한 1차 자료:

- [MLX 메모리 관리](https://ml-explore.github.io/mlx/build/html/python/memory_management.html): allocator cache와 활성 메모리의 구분 및 해제 API.
- [Swift Unlimited/DeepSeek OCR 지원 PR #473](https://github.com/ml-explore/mlx-swift-lm/pull/473): 2026-10-08 GitHub API로 다시 확인했으며 `state: open`, `merged: false`입니다.
- [4bit 모델과 양자화별 벤치마크](https://huggingface.co/sahilchachra/unlimited-ocr-4bit-mlx): 배포자 측정은 다른 하드웨어·스캔 폼 데이터에 대한 결과이며 KPaper 논문 품질 검증을 대신하지 않습니다.
