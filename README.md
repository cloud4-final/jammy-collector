관측 스택: https://github.com/cloud4-final/jammy-observability, 팀 인프라: https://github.com/cloud4-final/hybrid-5th

# Mobility Event Collector & Query API

3거점(jammy-a, jammy-b, jammy-c) 하이브리드 클라우드 환경에서 차량 원격 측정(Telematics) 데이터를 수집하고 조회하기 위한 백엔드 API 서비스입니다.

---

## 1. 컨테이너 이미지

- **공식 배포 이미지:**
  `ghcr.io/cloud4-final/jammy-collector:sha-REPLACE_ME`
  - `main` 브랜치 머지 후 CI/CD 파이프라인의 `Publish (.github/workflows/publish.yml)` 워크플로우에 의해 자동 빌드 및 게시됩니다.
- **비공개 이미지(private):** pull에 GitHub 인증이 필요합니다. 토큰은 `read:packages` 권한만 가진 classic PAT를 쓰고, 이 저장소에 읽기 권한이 있는 계정으로 발급합니다.
  - 로컬: `echo <PAT> | docker login ghcr.io -u <GitHub ID> --password-stdin`
  - K8s: 이미지를 쓰는 namespace마다 pull secret을 만들고, Deployment의 `imagePullSecrets`에 지정합니다(`deploy/k8s-example.yaml`에 반영됨).
    ```bash
    kubectl -n <namespace> create secret docker-registry ghcr-pull \
      --docker-server=ghcr.io --docker-username=<GitHub ID> --docker-password=<PAT>
    ```
  - 토큰을 가진 사람이 바뀌거나 토큰이 만료되면 pull이 실패합니다. 만료일을 정해 두고 갱신 담당을 정하세요.
- **로컬 개발/테스트 빌드:**
  ```bash
  docker build -t jammy-collector:dev .
  ```

---

## 2. 실행 방법 및 환경 변수

### 실행 방법
기본 포트는 `8000`입니다.

```bash
# 0. 데이터베이스 연결 URL 설정 (예시)
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres

# 1. DB 마이그레이션 (쓰기 원본 DB에만 1회 실행)
python -m src.migrate

# 2. 애플리케이션 서버 기동
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Docker로 실행 시:
```bash
docker run -d \
  --name mobility-collector \
  -p 8000:8000 \
  -e SITE_ID=jammy-a \
  -e DATABASE_URL=postgresql://collector_writer:password@postgres-primary:5432/mobility \
  -e DATABASE_READ_URL=postgresql://collector_reader:password@postgres-replica:5432/mobility \
  jammy-collector:dev
```

### 환경 변수 명세

| 환경 변수 | 기본값 | 필수 여부 | 설명 |
|---|---|---|---|
| `SITE_ID` | `local` | 선택 | 배포 거점 식별자 (`jammy-a`, `jammy-b`, `jammy-c`, `jammy-obs`) |
| `DATABASE_URL` | - | **필수** | PostgreSQL 쓰기 원본(Primary) 연결 URL (`postgresql://user:pass@host:port/dbname`) |
| `DATABASE_READ_URL` | `DATABASE_URL` | 선택 | PostgreSQL 읽기 전용(Replica) 연결 URL. 미지정 시 쓰기 URL과 동일 |
| `DB_POOL_MIN` | `1` | 선택 | psycopg ConnectionPool 최소 연결 수 |
| `DB_POOL_MAX` | `10` | 선택 | psycopg ConnectionPool 최대 연결 수 |
| `DEPLOY_ENV` | `dev` | 선택 | 배포 환경 (`dev`, `stage`, `prod`) |
| `OTEL_SERVICE_NAME` | `mobility-collector` | 선택 | OpenTelemetry Service Name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (미설정) | 선택 | OpenTelemetry OTLP/HTTP 수신 엔드포인트 (예: `http://127.0.0.1:4318`). 미설정 시 분산 추적 비활성화 |

---

## 3. API 계약 및 요청/응답 예시

### API 요약표

| 엔드포인트 | 메서드 | 성공 응답 | 실패 응답 |
|---|---|---|---|
| `/api/v1/events` | `POST` | 신규: `201 {"status":"accepted","event_id",...}`<br>중복: `200 {"status":"duplicate",...}` | 잘못된 region: `400`<br>DB 장애: `503 {"detail":"database unavailable"}` |
| `/api/v1/events/{event_id}` | `GET` | `200 {event_id, region, vehicle_id, speed_kmh, payload, site, received_at}` | 없음: `404`<br>DB 장애: `503` |
| `/api/v1/events` | `GET` | `200 {"items":[...], "count":n}`<br>(정렬: `received_at DESC, event_id DESC`) | `limit` 범위 초과: `422`<br>DB 장애: `503` |
| `/ready` | `GET` | `200 {"status":"ready","site":"..."}` | DB 연결 실패: `503 {"status":"not_ready","reason":"database"}` |
| `/healthz` | `GET` | `200 {"status":"ok","site":"..."}` | 프로세스 다운 시 무응답 |
| `/metrics` | `GET` | `200` (Prometheus 텍스트 포맷) | - |

### cURL 요청 및 응답 예시

#### 1) 신규 이벤트 수집 (POST 201)
```bash
curl -i -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "evt-1001",
    "region": "site-a",
    "vehicle_id": "v-101",
    "speed_kmh": 65.5,
    "payload": {"soc": 85, "temperature": 24.0}
  }'
```
응답:
```json
HTTP/1.1 201 Created
{
  "status": "accepted",
  "event_id": "evt-1001",
  "region": "site-a"
}
```

#### 2) 중복 이벤트 재전송 (POST 200)
동일한 `event_id`로 재요청 시 DB Conflict 처리되어 200 반환:
```json
HTTP/1.1 200 OK
{
  "status": "duplicate",
  "event_id": "evt-1001",
  "region": "site-a"
}
```

#### 3) 잘못된 거점 전송 시 (POST 400)
```bash
curl -i -X POST http://localhost:8000/api/v1/events \
  -H "Content-Type: application/json" \
  -d '{"event_id": "evt-bad", "region": "invalid-region", "vehicle_id": "v-1"}'
```
응답:
```json
HTTP/1.1 400 Bad Request
{
  "detail": "Invalid region 'invalid-region'. Allowed: ['aws', 'site-a', 'site-b', 'site-c']"
}
```

#### 4) 이벤트 단건 조회 (GET 200)
```bash
curl -i http://localhost:8000/api/v1/events/evt-1001
```
응답:
```json
HTTP/1.1 200 OK
{
  "event_id": "evt-1001",
  "region": "site-a",
  "vehicle_id": "v-101",
  "speed_kmh": 65.5,
  "payload": {"soc": 85, "temperature": 24.0},
  "site": "jammy-a",
  "received_at": "2026-11-05T02:45:00.123456+00:00"
}
```

#### 5) 이벤트 목록 필터 및 페이징 (GET 200)
```bash
# region, vehicle_id 필터 및 limit 지정
curl -i "http://localhost:8000/api/v1/events?region=site-a&limit=10"
```
응답:
```json
HTTP/1.1 200 OK
{
  "items": [
    {
      "event_id": "evt-1001",
      "region": "site-a",
      "vehicle_id": "v-101",
      "speed_kmh": 65.5,
      "payload": {"soc": 85, "temperature": 24.0},
      "site": "jammy-a",
      "received_at": "2026-11-05T02:45:00.123456+00:00"
    }
  ],
  "count": 1
}
```

#### 6) 헬스체크 및 준비상태 확인
- Liveness Probe: `curl http://localhost:8000/healthz` → `200 {"status":"ok","site":"jammy-a"}`
- Readiness Probe (DB 정상): `curl http://localhost:8000/ready` → `200 {"status":"ready","site":"jammy-a"}`
- Readiness Probe (DB 장애): `curl http://localhost:8000/ready` → `503 {"status":"not_ready","reason":"database"}`

### 샘플 데이터 및 배치 전송 스크립트
- `sample/events.jsonl`: 20건의 모빌리티 이벤트 데이터 (4개 region 고루 포함).
- `sample/send_events.sh`: 대량 이벤트 전송 스크립트.
  ```bash
  # 로컬 서버로 샘플 전송 (두 번째 인자로 다른 JSONL 파일 지정 가능)
  ./sample/send_events.sh http://127.0.0.1:8000
  ```
- 대량 시험용 데이터(시카고시 공개 택시 운행 데이터를 변환한 1,000건·9,870건)의 출처, 변환 규칙, 한계는 [`sample/DATA_SOURCES.md`](sample/DATA_SOURCES.md)에 있습니다. 파일 자체는 저장소에 없습니다.

---

## 4. 데이터베이스 및 복제 아키텍처

### 마이그레이션 (`sql/001_events.sql`)
- 마이그레이션 스크립트는 **반드시 쓰기 원본(Primary) 데이터베이스에서만 실행**해야 합니다.
  ```bash
  python -m src.migrate
  ```
- 테이블 및 인덱스 생성 구문은 `IF NOT EXISTS`로 작성되어 멱등(Idempotent)합니다.
- 스키마 `CREATE` 권한이 있는 계정으로 실행해야 합니다. 테이블이 이미 있어도 `INSERT`·`SELECT`만 가진 쓰기 계정으로 실행하면 `permission denied for schema public`으로 실패합니다. DB 관리자가 테이블을 미리 만들었다면 앱 배포 시 migrate는 생략합니다.

### 데이터베이스 역할 및 권한
- **쓰기 Role (Primary)**:
  - 권한: `events` 테이블에 대한 `INSERT`, `SELECT`
- **읽기 Role (Replica)**:
  - 권한: `events` 테이블에 대한 `SELECT`

### 쓰기/읽기 URL 분리 이유 (`DATABASE_URL` vs `DATABASE_READ_URL`)
- **거점별 역할 분리**: 거점 A가 Primary(원본 쓰기), 거점 B와 C가 Streaming Replica(사본 조회·반출)로 운영됩니다.
- **Failover / 승격 지원**: 거점 A 장애로 거점 B가 새로운 Primary로 승격될 경우, 애플리케이션 코드 수정 없이 K8s Secret의 `DATABASE_URL` 및 `DATABASE_READ_URL` 엔드포인트만 교체하여 무중단 전환을 달성합니다.

---

## 5. 관측 가능성 (Observability) 확인법

### Prometheus 메트릭 (`GET /metrics`)
- `http_requests_total{method, endpoint, status_code}`: HTTP 요청 총합. 엔드포인트 라벨은 라우트 템플릿(예: `/api/v1/events/{event_id}`)을 사용하여 카디널리티 폭증을 방지합니다.
- `http_request_duration_seconds{endpoint}`: HTTP 요청 지연시간 히스토그램.
- `mobility_events_collected_total{region, status}`: 거점별 수집 통계 (`accepted`, `duplicate`, `rejected`).
- `db_query_duration_seconds{operation}`: DB 작업별(`insert`, `get`, `list`, `ping`) 지연시간 히스토그램.

### 구조화된 JSON 로그
모든 로그는 JSON 형식으로 표준 출력(`stdout`)에 기록됩니다:
```json
{
  "timestamp": "2026-11-05 11:30:00,000",
  "level": "INFO",
  "message": "Event received: event_id=evt-1001, region=site-a, vehicle=v-101",
  "logger": "mobility-collector",
  "site": "jammy-a",
  "trace_id": "512f65e8299b0deefc32aff14818162a",
  "span_id": "2bbe212ea82a6916"
}
```
> **주의:** 개인정보 및 민감 데이터 보호를 위해 `payload` 원문은 로그에 절대 남기지 않습니다.

### OpenTelemetry 분산 추적
- `OTEL_EXPORTER_OTLP_ENDPOINT`를 설정하면 FastAPI 요청 및 psycopg DB 쿼리 추적 스팬(Span)이 자동으로 수집되어 Grafana Tempo로 전송됩니다.
- `/metrics`, `/healthz`, `/ready` 경로는 트레이스 수집에서 자동 제외됩니다.
- K8s 환경에서는 각 거점 Host에 떠 있는 Grafana Alloy OTLP 수신 포트(`http://<host-ip>:4318`)를 타겟으로 지정합니다. (방화벽 및 브리지 네트워크 합의 후 설정 안내 예정)
