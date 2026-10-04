# Green Action – AI API 명세 (BE/FE 공유용, v1)

> 기준일 2026-10-04 · AI 서버 경로는 모두 `/api` 아래 · **BE가 AI 서버를 호출**하는 내부 API입니다(앱이 AI 서버를 직접 부르지 않음).
> 필드 이름과 설문 옵션 코드는 BE 설문 문항이 확정되면 맞출 수 있으니 바뀌면 알려 주세요.

## 0. 전체 흐름

1. **설문 완료** → BE가 답을 저장하고 `POST /api/profile` 호출 → 응답(프로필)을 저장합니다.
2. **챌린지 추천** → BE가 저장된 프로필로 `POST /api/challenges/recommend` 호출 → 응답의 챌린지 3개를 사용자에게 부여(저장)합니다.
3. **수행·완료·포인트·레벨업**은 전부 BE 몫입니다. (AI는 점수를 계산하거나 지급하지 않습니다.)
4. **영수증이 쌓인 뒤**(최근 30일 확정 5건 이상) BE가 `POST /api/profile`을 **`user_id`와 함께** 다시 호출하면 설문 대신 실제 소비로 계산한 프로필(`source: "data"`)이 옵니다. 필요하면 그 프로필로 다시 추천을 받습니다.

## 1. 설문 문항과 응답 코드

| 필드 | 문항 | 선택지 → 코드 |
|---|---|---|
| `transport` | 1. 이동은 주로 뭘로 하세요? | 자가용 `car` / 대중교통 `public_transit` / 도보·자전거 `walk_bike` |
| `transport_spend` | 1-1. (자가용) 한 달 자동차 관련 지출 | 10만원 이하 `under_100k` / 10~30만원 `100k_to_300k` / 30만원 이상 `over_300k` |
| | 1-1. (대중교통) 한 달 대중교통비 | 3만원 이하 `under_30k` / 3~7만원 `30k_to_70k` / 7만원 이상 `over_70k` |
| | (도보·자전거는 후속 질문 없음) | |
| `cafe_drink` | 2. 한 달 카페·음료 지출 | 거의 없음 `none` / 3만원 이하 `under_30k` / 3만원 이상 `over_30k` |
| `food` | 3. 한 달 식품(식재료·외식) 지출 | 10만원 이하 `under_100k` / 10~30만원 `100k_to_300k` / 30만원 이상 `over_300k` |
| `shopping` | 4. 한 달 온·오프라인 쇼핑 지출 | 5만원 이하 `under_50k` / 5~15만원 `50k_to_150k` / 15만원 이상 `over_150k` |
| `eco_interest` | 5. 친환경 제품을 써 본 적 있나요? | 별로 관심 없음 `not_interested` / 없지만 관심 있음 `interested_not_tried` / 있음 `tried` |
| `goal_intent` | 6. 이번 달 각오는요? | 가볍게 시작 `light_start` / 확실히 줄여보기 `serious_reduction` / 일단 구경만 `just_looking` |

모든 필드는 선택(Optional)이며, 비어 있으면 가장 부담 적은 쪽으로 처리합니다.

## 2. `POST /api/profile` — Green Profile 생성

**요청** (설문 답 + 선택 항목 `user_id`)
```json
{
  "transport": "car",
  "transport_spend": "100k_to_300k",
  "cafe_drink": "under_30k",
  "food": "100k_to_300k",
  "shopping": "50k_to_150k",
  "eco_interest": "interested_not_tried",
  "goal_intent": "serious_reduction",
  "user_id": 1,
  "recent_challenge_completions": 0
}
```
`user_id`를 보내면 최근 30일 **확정(SUCCESS)** 영수증이 5건 이상일 때 실데이터로 계산하고, 모자라면 설문으로 계산합니다.
`recent_challenge_completions`(선택, 기본 0)는 **최근 30일에 완료한 챌린지 수**(BE가 센 값)입니다. 설문은 처음 한 번만 하므로, 이 값이 2 이상이면 설문 답과 상관없이 태도 축이 **실행가(A, 70% 이상)**로 올라갑니다(내려가지는 않음).

**응답 (200)**
```json
{
  "source": "survey",
  "data_info": { "used": false, "receipts_in_window": 2, "window_days": 30, "min_receipts": 5, "reason": "insufficient_receipts" },
  "areas": [
    { "key": "move", "label": "이동", "level": 4, "level_label": "높음",     "carbon_kg": 270.63,  "spend_krw": 200000 },
    { "key": "food", "label": "식품", "level": 3, "level_label": "보통",     "carbon_kg": 35.036,  "spend_krw": 200000 },
    { "key": "cafe", "label": "카페", "level": 2, "level_label": "낮음",     "carbon_kg": 2.628,   "spend_krw": 15000 },
    { "key": "shop", "label": "쇼핑", "level": 3, "level_label": "보통",     "carbon_kg": 25.448,  "spend_krw": 100000 }
  ],
  "persona": {
    "type_code": "DLSA", "type_name": "쭉쭉 자라는 전나무", "emoji": "🌲",
    "tagline": "바쁘게 달리고 알뜰히 사는 전나무",
    "description": "자가용 이동과 쇼핑이 눈에 띄어요. 실천 의지가 높으니 이동과 쇼핑 챌린지를 번갈아 도전해 보세요.",
    "axes": [
      { "axis": "이동", "letter": "D", "label": "드라이버", "percent": 78,
        "opposite_letter": "W", "opposite_label": "뚜벅이", "opposite_percent": 22 },
      { "axis": "식탁", "letter": "L", "label": "가벼운 식탁", "percent": 52,
        "opposite_letter": "F", "opposite_label": "풍성한 식탁", "opposite_percent": 48 },
      { "axis": "소비", "letter": "S", "label": "쇼퍼", "percent": 73,
        "opposite_letter": "M", "opposite_label": "미니멀", "opposite_percent": 27 },
      { "axis": "태도", "letter": "A", "label": "실행가", "percent": 85,
        "opposite_letter": "E", "opposite_label": "탐색가", "opposite_percent": 15 }
    ],
    "preferred_difficulty": 2
  },
  "focus_area": "move",
  "baseline_carbon_kg": 333.742,
  "reduction_rate": 0.15,
  "target_carbon_kg": 283.681,
  "message": "확실하게 줄여볼 준비가 되셨네요! 적극적으로 도전해봐요."
}
```

| 필드 | 설명 / 화면에서 쓰는 법 |
|---|---|
| `source` | `"survey"`(설문 기반) / `"data"`(최근 30일 영수증 기반) |
| `data_info` | `user_id`를 보냈을 때만 있음(없으면 `null`). `used`, 영수증 수, 최소 조건, `reason` |
| `areas[]` | 이동·식품·카페·쇼핑 4개. `level` 1~5(매우 낮음~매우 높음)로 "Green Profile" 막대를 그림. `carbon_kg`는 월 예상 배출량 |
| `persona` | **GSTI(Green Step Type Indicator) 유형**(4축 16유형, 식물·자연 테마). `type_code`(예 `DLSA`), 이름, 이모지, 한 줄 소개, 설명, 축 4개(`axes`, 축마다 선택된 쪽 `percent`(50~95)와 반대쪽 `opposite_percent`, 합 100 — MBTI식 비율 막대용. 기준선에서 멀수록 한쪽에 가깝고, 태도 축은 설문 답 조합별 고정값). `preferred_difficulty`는 추천에 쓰는 값이라 화면엔 안 보여도 됨 |
| `focus_area` | 먼저 시작할 영역 키(`move`/`food`/`cafe`/`shop`), 모두 1레벨이면 `null` |
| `baseline_carbon_kg` | 월 예상(또는 실제) 배출량 |
| `reduction_rate`, `target_carbon_kg` | 이번 달 추천 목표. **"일단 구경만"이면 둘 다 `null`** → 목표 대신 기준 배출량만 보여 주세요 |
| `message` | 각오에 맞춘 안내 문구 |

- **설문은 처음 한 번만 하고, 이후 GSTI는 데이터로 자동 변경됩니다**(앞 세 축은 영수증·소비 데이터, 태도 축은 챌린지 완료 이력). BE가 하루 1회 저장된 설문 답 + `user_id` + `recent_challenge_completions`로 `/api/profile`을 다시 호출해 갱신하는 방식을 권합니다. 유형이 바뀌었을 때 "무엇에서 무엇으로 바뀌었는지" 안내를 화면에 두는 걸 권합니다.
- **앱 문구에는 "MBTI"라는 말을 쓰지 말고 "GSTI"**로 불러 주세요. MBTI는 Myers & Briggs Foundation의 등록상표입니다(정품 검사만 지칭하도록 사용 지침이 있음). GSTI는 우리가 만든 별개의 이름이고 글자 구성(W/D·L/F·M/S·A/E)과 유형 이름도 다르지만, 정식 출시 전에는 특허정보검색(KIPRIS)에서 유사 상표를 확인해 주세요.
- 오류: 필드 타입이 틀리면 `422`(어느 필드인지 알려 줌), 서버 내부 문제는 `500`과 원인.

## 3. `POST /api/challenges/recommend` — 맞춤 챌린지 3개

**요청** — `/api/profile` 응답을 **통째로** `profile`에 넣어도 됩니다(모르는 필드는 무시).
```json
{
  "profile": { "...": "/api/profile 응답 그대로" },
  "exclude_challenge_ids": ["MOVE_1"],
  "user_name": "테스트유저"
}
```
- `profile`은 **필수**입니다. `user_id`만 보내면 `400`입니다(실데이터 기반 추천은 `/api/profile`을 `user_id`와 함께 먼저 호출한 뒤 그 응답을 넘겨 주세요).
- `exclude_challenge_ids`: 이미 부여했거나 완료한 챌린지를 빼고 싶을 때.
- `user_name`(선택): 추천 이유 문구에서 "OO님"으로 부를 이름. GSTI 유형 이름(예: 느긋한 고구마)은 재미로 보는 결과라 추천 이유 문구에는 쓰지 않습니다. 이름이 없으면 이름 없이 작성하고, 한글·영문·숫자·공백만 남기고 20자로 줄여서 사용합니다. AI가 쓰지 못하는 상황이면 고정 문구 앞에 "OO님, "이 붙습니다.

**응답 (200)**
```json
{
  "source": "survey",
  "intro": "AI가 생활패턴을 분석했어요.",
  "challenges": [
    {
      "slot": 1,
      "challenge_id": "MOVE_2",
      "area": "move", "area_label": "이동", "difficulty": 2,
      "title": "대중교통 주 2회 이용하기",
      "description": "이번 주 대중교통을 2회 이용해 보세요.",
      "target_count": 2, "unit": "회", "period": "week",
      "verification": "AUTO_TRANSIT", "verification_next": null, "daily_check_limit": null,
      "points": 70, "est_saving_kg": 7.92, "saving_basis": "MOVE_1 값 x 2회",
      "reason": "바쁜 일상 속에서도 대중교통을 이용하시면 이동 탄소 발자국을 줄일 수 있어요. 알뜰하게 포인트도 챙겨가세요!",
      "reason_source": "llm"
    },
    { "slot": 2, "challenge_id": "FOOD_1", "...": "..." },
    { "slot": 3, "challenge_id": "SHOP_1", "...": "..." }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `slot` | 추천 순서 1~3 (1번이 가장 줄일 여지가 큰 영역) |
| `difficulty` | 1~3 → 화면의 난이도 점(●○○, ●●○, ●●●) |
| `verification` | `AUTO_TRANSIT` = 대중교통 GPS 인증으로 **자동 완료**, `SELF` = 사용자가 직접 체크 |
| `daily_check_limit` | 자율 체크(`SELF`)는 **챌린지당 하루 1회**만 체크 가능, 자동 인증은 `null` |
| `target_count`, `unit`, `period` | 달성 기준(예: 이번 주 2회). 기간은 지금 전부 `week` |
| `points` | 완료 시 지급할 포인트 — **지급은 BE가** |
| `est_saving_kg` | **예상** 절감량(가정치). 화면에 "예상"으로 표기하고, `null`이면 표시하지 않기 |
| `reason`, `reason_source` | 추천 이유 문구. `llm`이면 AI가 쓴 문장, `fallback`이면 고정 문구(AI 호출 실패 시). 둘 다 그대로 보여 주면 됨 |

- 난이도·포인트·절감량·인증방식은 **카탈로그의 고정값**이고 LLM이 바꾸지 않습니다.
- `reason`은 키 없음·시간 초과(6초)·형식 오류면 고정 문구로 대체되어 **응답은 항상 200**입니다.
- 같은 프로필·같은 제외 목록이면 항상 같은 챌린지가 선택됩니다(문구만 달라질 수 있음).
- 오류: 입력 형식 오류 `422`, `profile` 없음 `400`, 서버 내부 문제 `500`.

## 4. `GET /api/challenges/catalog` — 챌린지 카탈로그 15개

BE가 챌린지 테이블을 채울 때(seed) 쓰는 원본입니다. 5영역(이동·카페·식품·쇼핑·생활) × 3난이도입니다. 항목 모양은 3번의 챌린지 항목에서 `slot`, `reason`, `reason_source`만 뺀 것이고, 응답 맨 위에 `catalog_version`이 있습니다.

| 영역 | 난이도 1 | 난이도 2 | 난이도 3 | 인증 |
|---|---|---|---|---|
| 이동 | 대중교통 1회 (30P) | 주 2회 (70P) | 주 3회 이상 (120P) | 자동(GPS) |
| 카페 | 텀블러 1회 (10P) | 주 3회 (30P) | 주 5회 (50P) | 자율 체크 |
| 식품 | 저탄소 식품 한 끼 (10P) | 주 2회 (30P) | 고탄소 식품 줄이기 (50P) | 자율 체크 |
| 쇼핑 | 구매 1회 미루기 (10P) | 무구매 1일 (30P) | 무구매 2일 (50P) | 자율 체크 |
| 생활 | 전등·대기전력 체크 (10P) | 주 3회 (30P) | 1주 유지 (50P) | 자율 체크 |

포인트와 인증방식은 AI 쪽 제안값이라 **기획 확정 후 JSON 한 파일(`app/data/challenge_catalog.json`)만 고치면 됩니다.** 카탈로그는 서버가 읽을 때마다 구조를 검사해서, 값이 어긋나면 `500`과 원인을 돌려줍니다.

## 4-1. 챌린지의 인증 방식 필드 (recommend / catalog 응답에 추가됨)

| 필드 | 설명 |
|---|---|
| `verification_methods` | 화면에 보여 줄 인증 방식. `["AUTO_TRANSIT"]`(대중교통 GPS 자동), `["SELF"]`(직접 체크), `["SELF","PHOTO"]`(직접 체크 + 사진 인증) |
| `photo_verification` | 사진 인증 종류 `"TUMBLER"`(텀블러+카페 영수증) / `"LOW_CARBON"`(저탄소 인증 마크+영수증) / `null` |
| `self_check_limit` | 사진 인증이 있는 챌린지의 **직접 체크 주간 인정 횟수**. 텀블러는 목표의 절반(내림, 1회짜리는 0 = 인증 필수), 저탄소 마크는 절반(올림). 사진 인증이 없으면 `null`(제한 없음) |
| `cross_check` | 서버 교차 검증. `"NO_SHOPPING_RECEIPT"`(무구매 챌린지: 그날 쇼핑 영수증이 등록돼 있으면 인정하지 않음) / `null` |

BE는 부여할 때 이 값을 챌린지에 스냅샷으로 저장해 두고 규칙을 적용합니다 (자세한 동작은 BE 저장소 `GREEN_ACTION.md`).

## 4-2. `POST /api/challenges/verify` — 사진 인증 판정 (multipart)

사진(텀블러/인증 마크 상품 + 영수증)을 읽어 인증 여부를 정합니다. **사진은 저장하지 않습니다.**

**요청** (`multipart/form-data`): `kind` = `TUMBLER` | `LOW_CARBON`, `images` = 사진 1~3장(장당 8MB 이하)

**판정 원칙: 한 사진 안에서 조건이 모두 맞아야 합니다.** AI가 사진마다 따로 읽고, 텀블러(또는 인증 마크 상품)와 읽히는 최근 영수증이 **같은 사진**에 함께 있는 사진이 한 장이라도 있으면 통과합니다. 텀블러 사진 따로·영수증 사진 따로 내면 합치지 않고 `SEPARATE_PHOTOS`로 거절합니다(집에서 찍은 텀블러 + 일회용 컵으로 산 영수증 같은 악용 방지). 영수증 자체에 개인컵 할인·에코별 줄이 찍혀 있으면(매장 POS 기록) 영수증만으로 통과합니다(`CHALLENGE_ALLOW_DISCOUNT_ONLY=false`로 끌 수 있음). 여러 장을 보내는 것은 흐린 사진을 대비한 "다시 찍은 사진"용입니다.

**응답** (통과/거절 모두 200)
```json
{
  "passed": true,
  "code": "OK",
  "message": "인증 완료! 영수증의 개인컵 할인 문구로 확인됐어요.",
  "kind": "TUMBLER",
  "receipt": {"merchant_name": "스타벅스 강남점", "payment_date": "2026-10-04", "payment_time": "13:41", "total_amount": 4100, "fingerprint": "e29c8ae71e..."},
  "evidence": "RECEIPT_DISCOUNT",
  "marks": []
}
```
- `code`: `OK`, `RECEIPT_UNREADABLE`(영수증의 가맹점·날짜·금액을 못 읽음), `RECEIPT_DATE`, `RECEIPT_TOO_OLD`(결제 시각이 읽히면 **24시간 안**, 안 읽히면 오늘·어제 영수증만 인정), `RECEIPT_EDITED`(편집 흔적), `NOT_CAFE`, `NO_TUMBLER`, `NO_MARK`, `SEPARATE_PHOTOS`(텀블러/마크와 영수증이 서로 다른 사진에 있음)
- `evidence`(통과 근거): `PHOTO_AND_RECEIPT`(텀블러 사진 + 영수증 개인컵 할인), `PHOTO`(텀블러 사진 + 카페 영수증), `RECEIPT_DISCOUNT`(영수증의 개인컵/에코별 문구), `MARK_AND_RECEIPT`(인정 마크 + 영수증)
- `receipt.fingerprint`: 날짜·시각·금액의 해시. **BE가 DB 유니크로 중복 사용을 막습니다** (같은 영수증을 다시 찍어도 같은 값이 나옴, 저화질 재촬영으로 확인).
- 오류: 사진 형식 문제 `400`(`detail`에 문구), AI 일시 불가 `503`. `CHALLENGE_VERIFY_ENABLED=false`로 기능을 끌 수 있습니다.
- 인정 마크: 탄소성적표지 중 **저탄소제품** 단계(환경부), 저탄소 농축산물 인증(농림축산식품부), 저탄소 축산물 인증. 탄소배출량만 표시된 1단계 표지와 친환경·유기농 마크는 인정하지 않습니다.
- 한 번 호출에 보통 1.5~3초, 서버 기동 직후 첫 호출은 준비되지 않았다면 더 걸릴 수 있어 서버 기동 때 미리 준비합니다. BE 타임아웃은 40초를 권합니다.

## 4-3. 가정 에너지(관리비) API

### `POST /api/household/read-bill` (multipart `images` 1~3장)
관리비/공과금 고지서 사진을 읽는다. 사진은 저장하지 않는다. 읽기 결과는 통과/실패 모두 200이다.
```json
{"readable": true, "code": "OK", "message": "고지서에서 값을 읽었어요. 맞는지 확인하고 저장해 주세요.",
 "bill_month": "2026-09",
 "values": {"electricity_kwh": 320.0, "electricity_krw": 58120, "water_m3": 14.0, "water_krw": 9800,
            "gas_m3": 28.0, "gas_krw": 31050, "heat_gcal": null, "heat_krw": null},
 "total_krw": 182370, "fingerprint": "…", "note": null}
```
- 실패 `code`: `NOT_A_BILL`, `UNREADABLE`, `EDITED`, `MONTH_UNKNOWN`, `MONTH_OUT_OF_RANGE`(최근 13개월 밖), `NO_VALUES`
- 세대 개별 사용료만 읽고 공용요금은 제외한다. 값이 상식 범위를 벗어나면 버리고 `note`로 알린다. 사진 형식 문제 `400`, AI 불가 `503`.
- `fingerprint`는 (월 + 읽은 값) 해시이며 BE가 같은 고지서의 다른 계정 재사용을 막는 데 쓴다.

### `POST /api/household/carbon` (JSON)
`electricity_kwh/_krw`, `water_m3/_krw`, `gas_m3/_krw`, `heat_gcal/_krw`(모두 선택)로 한 달 탄소를 계산한다.
사용량이 있으면 `사용량 × 배출계수`(전기 0.4173, 수도 0.237, 가스 2.176, 지역난방 146.9), 없고 금액만 있으면 원당 계수로 추정(`basis: "spend"`).
응답: `total_kg`, `items[{key,label,usage,unit,krw,carbon_kg,basis}]`, `estimated`, `note`. 근거와 한계는 `GreenAction_household_evidence.md`.

## 5. 호출 팁

- **응답 시간(로컬 측정):** `/profile`은 DB만 쓰는 계산이라 설문 기준 약 0.2초, `user_id`로 영수증을 같이 조회하면 약 0.4초입니다. `/recommend`는 AI 문구 생성이 들어가서 보통 1~3초입니다. BE 타임아웃은 **10초** 정도를 권합니다.
- **AI 서버는 외부에 열려 있지 않습니다.** BE가 내부 주소(`AI_SERVICE_URL`)로 호출합니다.
- **재추천 시점**(예: 매주 월요일, 챌린지를 모두 완료했을 때)과 `exclude_challenge_ids` 관리는 BE가 정해 주세요.

## 6. 팀에서 정해 주세요 (미정)

1. **레벨별 필요 포인트**(캐릭터 레벨업 기준은 챌린지 포인트로 확정, 임계값만 미정. 임시안: 100 / 300 / 600). 기존 친환경 활동 포인트와 합산할지도.
2. **카탈로그 값**(포인트, 인증방식, `FOOD_3` "고탄소 식품 줄이기"의 정의).
3. **설문 문항·응답 코드**(위 1번 표를 BE 문항에 맞추기).
4. **재추천 주기**와 완료한 챌린지를 어떻게 제외할지.

## 7. 알려진 한계

- 자율 체크 챌린지 중 절감량(`est_saving_kg`)이 있는 건 이동·쇼핑 6개뿐입니다(출처 있는 값이 없는 나머지는 `null`).
- 실데이터 프로필은 **영수증을 올린 소비만** 반영합니다. 그래서 최소 5건 조건을 두었고, 이동은 설문 추정값보다 낮아지지 않게 했습니다(자가용 연료비는 영수증으로 잘 안 올라옴).
- 실데이터 조건(30일, 5건)과 그린 유형 판정 기준은 **잠정값**입니다.
