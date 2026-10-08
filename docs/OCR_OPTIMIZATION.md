# OCR 메모리 최적화

KPaper 0.3.5는 기본 Unlimited-OCR MXFP8 모델을 유지하면서 PDF 렌더링, 비전 연산의 일시 메모리와 모델 수명을 제한합니다. Python 인터프리터를 제거하는 것만으로 모델 가중치와 Metal 연산 메모리가 없어지지는 않습니다. 현재 추론은 MLX/Metal에서, PDF 래스터 생성은 PyMuPDF의 네이티브 코드에서 수행합니다.

## 적용한 변경

- 전체 스크린샷 목록 대신 한 페이지씩 렌더링합니다. 저장된 페이지 PNG는 원본 그림·표·수식 crop에 계속 사용합니다.
- 비전 인코더의 여러 crop을 순서대로 계산하고 평가합니다. 입력 해상도, crop 순서, 가중치는 유지합니다.
- SAM 전역 attention은 전체 key/value를 유지한 채 query를 256개씩 계산합니다. 큰 상대 위치 행렬을 한 번에 생성하지 않습니다. 원래 upstream attention과 계산 결과의 일치 여부를 테스트합니다.
- MLX allocator 재사용 캐시를 256MiB로 제한합니다. PDF가 끝나거나 실패하면 모델·프로세서·Metal 캐시를 명시적으로 해제합니다. 이 캐시 한도는 전체 프로세스 메모리의 상한이 아닙니다.
- 사용자별 파일 잠금으로 여러 KPaper 프로세스의 OCR 모델 중복 로드를 막습니다. 기다리는 작업은 진행 상태에 대기를 표시하며, 모델이 필요한 시점에만 잠금을 잡습니다.
- 스캔 또는 그림·벡터 도형·수식의 징후가 있는 페이지에는 grounded 분석을 유지합니다. 명확한 일반 텍스트 페이지는 native 추출을 사용합니다.
- 페이지 이미지, 모델의 로컬 HF revision/파일 정보, 최대 출력 토큰과 처리 버전별로 완료된 grounded 결과를 캐시합니다. 모델 로드 전에 캐시를 확인하며, 잘린 응답은 저장하지 않습니다.

## 측정 범위

2026-10-08, Apple M4 24GB, `kaldi-2011.pdf` 첫 3페이지. 기본 `sahilchachra/unlimited-ocr-mxfp8-mlx`, 144dpi, 최대 8192토큰, 모델 파일은 이미 내려받은 상태입니다. 번역 API는 호출하지 않았습니다. 다운로드 시간은 포함하지 않습니다.

| 항목 | 0.3.4 | 0.3.5 |
| --- | ---: | ---: |
| PDF 가져오기 시간 | 52.7초 | 46.1~50.1초 (2회) |
| MLX 최대 할당량 | 5.79GB | 4.40GB |
| 분석 후 MLX 재사용 캐시 | 3.98GB | 0GB |
| 텍스트 블록 (제목 포함) | 59 | 59 |
| 재처리 시간 (유효한 캐시 있음) | 없음 | 약 0.5초, 모델 로드 없음 |

MLX 최고치는 약 24% 감소했습니다. 이는 단일 로컬 PDF의 측정이며, Activity Monitor의 전체 프로세스 메모리나 모든 문서의 절감률을 뜻하지 않습니다. 모델 자체의 가중치 메모리는 계속 필요합니다. 재실행에서는 유효한 캐시를 재사용해 모델을 로드하지 않습니다. 동일 해상도라도 렌더러·부동소수점 연산에 따라 grounded box와 모델 출력이 조금 달라질 수 있습니다. 본문은 디지털 PDF의 native 텍스트를 유지하며, 원본 시각 자료 crop도 확인합니다.

CLI로 측정을 확인할 수 있습니다:

```bash
./kpaper pdf-import --paper-id my-paper --pdf /path/to/paper.pdf --max-pages 3 --json
```

`ocr_runtime.peak_mlx_bytes`는 모델의 MLX 할당 최고치이고, `generated_pages`와 `cache_hits`는 이번 실행에서 생성 또는 재사용한 페이지 수입니다. `native_only_pages`는 모델이 필요 없는 일반 텍스트 페이지입니다. 캐시를 강제로 새로 만들려면 해당 논문의 `inputs/assets/<paper-id>/layout/cache/`만 제거하면 됩니다. `--force`는 PDF 다운로드 덮어쓰기 옵션이며 OCR 캐시 삭제 옵션이 아닙니다.

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
- [Swift Unlimited/DeepSeek OCR 지원 PR #473](https://github.com/ml-explore/mlx-swift-lm/pull/473): 2026-10-08 확인 시 미병합 상태.
- [4bit 모델과 양자화별 벤치마크](https://huggingface.co/sahilchachra/unlimited-ocr-4bit-mlx): 배포자 측정은 다른 하드웨어·스캔 폼 데이터에 대한 결과이며 KPaper 논문 품질 검증을 대신하지 않습니다.
