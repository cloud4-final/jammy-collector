# 샘플·시험 데이터 출처

이 저장소의 시험 데이터가 어디서 왔고 어떤 데이터인지, 어디서 다시 구할 수 있는지 기록합니다.

| 데이터 | 건수 | 출처 | 저장소 포함 |
|---|---|---|---|
| `sample/events.jsonl` | 20 | 직접 작성한 합성 데이터 (4개 region 고루, 기능 확인용) | 포함 |
| Chicago 택시 운행 변환본 (소) | 1,000 | 아래 공개 데이터 | **미포함** |
| Chicago 택시 운행 변환본 (대) | 9,870 | 아래 공개 데이터 | **미포함** |

Chicago 변환본은 부하·조회 시험용으로 만들었습니다. 파일은 팀 내에서 직접 전달하고, 저장소에는 올리지 않습니다(아래 "이용 조건" 참고).

## 원본: City of Chicago, Taxi Trips (2024-)

| 항목 | 내용 |
|---|---|
| 제공처 | City of Chicago (시카고시, 택시 규제 기관에 보고된 운행 기록) |
| 데이터셋 | Taxi Trips (2024-), 식별자 `ajtu-isnz` |
| 페이지 | https://data.cityofchicago.org/Transportation/Taxi-Trips-2024-/ajtu-isnz |
| API (CSV) | `https://data.cityofchicago.org/resource/ajtu-isnz.csv` (Socrata SoQL) |
| 이용 조건 | 포털 표기는 "See Terms of Use". https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html |
| 출처 표기 | City of Chicago |
| 확인일 | 2026-11-06 (포털 기준 데이터 갱신일 2026-10-09) |

- 한 행은 택시 운행 1건입니다: 시작·종료 시각, 운행 시간(초), 거리(mile), 승하차 구역, 요금·팁·합계, 결제 방식, 운행 회사.
- 비식별 처리는 제공처가 이미 적용했습니다. `taxi_id`는 같은 택시면 같은 값이지만 차량 번호는 알 수 없는 해시이고, 시각은 15분 단위로 반올림되어 있으며, 일부 census tract는 비공개입니다.
- 2023년까지의 운행은 별도 데이터셋(Taxi Trips 2013-2023, `wrvz-psew`)입니다.

## 받은 방법

한 번에 1만 건을 요청하면 서버가 100초 넘게 응답하지 않아(시간 초과) 시간 구간을 나눠 받았습니다.

- 구간: 2024-03-04 00:00 ~ 2024-03-11 00:00을 1시간 단위 168개로 나눔
- 구간당 최대 60건, 6개 병렬 요청, 실패 시 3회 재시도 (1개 구간은 결과 없음) → 9,889건
- 조건: `trip_seconds > 60`, `trip_miles > 0.2`, `taxi_id` 있음

한 구간을 받는 예:

```bash
curl -G 'https://data.cityofchicago.org/resource/ajtu-isnz.csv' \
  --data-urlencode '$limit=60' \
  --data-urlencode '$select=trip_id,taxi_id,trip_start_timestamp,trip_end_timestamp,trip_seconds,trip_miles,pickup_community_area,dropoff_community_area,fare,tips,trip_total,payment_type,company' \
  --data-urlencode '$where=trip_start_timestamp >= "2024-03-04T00:00:00" and trip_start_timestamp < "2024-03-04T01:00:00" and trip_seconds > 60 and trip_miles > 0.2 and taxi_id is not null'
```

소 세트(1,000건)는 2024-03-01 하루 구간에서 1,500건을 한 번에 받아 앞 1,000건을 썼습니다. 그래서 시간대가 2024-03-01 20:30 ~ 03-02 00:00에 몰려 있습니다.

## 컬럼 대응과 변환

| 이 저장소(`events`) | 원본 | 규칙 |
|---|---|---|
| `event_id` | `trip_id` | `"chi-"` + 앞 16자 |
| `vehicle_id` | `taxi_id` | `"taxi-"` + 앞 12자 (원본은 128자 해시, 줄여도 충돌 없음) |
| `speed_kmh` | `trip_seconds`, `trip_miles` | `miles × 1.609344 / (seconds / 3600)`, 소수 첫째 자리 |
| `region` | 없음 | `sha256(taxi_id) mod 3`으로 `site-a`/`site-b`/`site-c`에 배정 |
| `payload` | 나머지 | 시작·종료 시각, 시간·거리, 승하차 구역, 요금·팁·합계, 결제 방식, 회사, `source` |
| `site`, `received_at` | 없음 | API가 저장할 때 채움 (`SITE_ID`, 적재 시각) |

정제: 대 세트는 평균 속도가 1 km/h 미만이거나 120 km/h를 넘는 행을 원본 입력 오류로 보고 제외했습니다(19건, 최대 229 km/h). 소 세트는 이 정제 전에 만들어 속도 범위만 `speed_kmh` 제약(0~400)으로 걸렀습니다.

## 결과

| | 소 | 대 |
|---|---|---|
| 건수 | 1,000 | 9,870 |
| 기간 | 2024-03-01 20:30 ~ 03-02 00:00 | 2024-03-04 ~ 03-10 (24시간대 모두) |
| 차량 | 569대 | 2,130대 (차량당 중앙값 4건, 최대 63건) |
| region | site-a 365 / site-b 300 / site-c 335 | site-a 3,329 / site-b 3,282 / site-c 3,259 |
| 평균 속도 | 0.4 ~ 111.2 km/h | 1.0 ~ 119.5 km/h |
| SHA-256 | `36f20f0f9127de38…` | `f9e8534656171839…` |

모든 이벤트는 `MobilityEvent` 검증을 통과했고, `event_id`는 전부 고유합니다.

## 한계

- **`region`은 가정입니다.** 시카고 택시에는 우리 거점 개념이 없어서 해시로 임의 배정했습니다. 같은 차량은 항상 같은 거점으로 가지만 실제 위치와는 무관합니다.
- **`received_at`은 원래 시각이 아닙니다.** API는 저장 시각(`now()`)을 기록합니다. 운행의 실제 시각은 `payload.trip_start`, `payload.trip_end`에 있습니다.
- **`speed_kmh`는 평균 속도입니다.** 거리를 시간으로 나눈 값이라 순간 속도가 아닙니다.
- 시카고 택시 운행이므로 값 분포는 이 프로젝트의 실제 차량 데이터와 다릅니다. 기능·조회·부하 시험용으로만 쓰고 분석 결과를 실제 서비스 지표로 해석하지 마세요.

## 이용 조건

포털이 라이선스를 "See Terms of Use"로만 표기하고 재배포 조건을 명확히 밝히지 않아, **데이터 파일은 저장소에 커밋하지 않습니다.** 필요한 사람이 위 방법으로 직접 내려받거나 팀 내 전달받은 파일을 사용하세요. 사용 시 출처로 City of Chicago를 표기합니다.

## 사용법

```bash
# 20건 (저장소에 포함)
./sample/send_events.sh http://127.0.0.1:8000

# 전달받은 변환 파일 (두 번째 인자로 경로 지정)
./sample/send_events.sh http://127.0.0.1:8000 /path/to/jammy-events_chicago_9870.jsonl
```

스크립트는 한 줄씩 순서대로 `POST /api/v1/events`를 보냅니다. 같은 `event_id`를 다시 보내면 `200 duplicate`이므로 반복 실행해도 데이터가 늘지 않습니다.
