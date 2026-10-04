# 챌린지 인증 방식 확장 — 조사 근거와 구현 정리 (2026-10-04)

사용자 제안 5개(GPS 속도 검증, 텀블러·저탄소 마크 사진 인증, 자율 체크+인증 하이브리드, 쇼핑)를
인터넷 자료로 타당성을 확인하고 구현한 기록입니다. "무엇을 근거로 어떻게 정했는지"와 "아직 확인 못 한 것"을 구분해 적었습니다.

## 1. 한눈에 보는 결과

| 제안 | 결과 | 구현 위치 | 검증한 방법 |
|---|---|---|---|
| 1. GPS 속도 기반 대중교통 검증 | 구현 (버스 vs 자가용은 구분 불가, 한계 명시) | BE `TransitTripAnalyzer`, FE 경로 기록 | 합성 경로 단위 테스트 13개 + 통합 테스트 7개 |
| 2. 텀블러 + 영수증 사진 인증 | 구현 (한 사진 안에 함께 있어야 인정) | AI `challenge_verifier`, BE `/green/challenges/{id}/verify`, FE `ChallengeVerifyScreen` | 실제 Gemini로 합성 영수증 판독 + 통합 테스트 |
| 3. 저탄소 인증 마크 사진 인증 | 구현 (마크 **실사진 정확도는 미검증**) | 위와 같은 인프라 (`LOW_CARBON`) | 규칙·거절 경로는 테스트, 마크 판독은 합성으로 불가 |
| 4. 자율 체크 + 인증 하이브리드 | 구현 | 카탈로그 `self_check_limit`, BE 체크/인증 규칙, FE 두 버튼 | 통합 테스트 |
| 5. 쇼핑 | "무구매"만 영수증 교차 검증으로 구현, 나머지는 자율 체크 | BE `dropConflictingCheckIns` 등 | 통합 테스트 |

## 2. 항목별 근거

### 2-1. GPS 속도 기반 검증 (제안 1)
**근거 자료**
- 스마트폰 GPS로 이동수단을 판별하는 연구들은 속도 구간으로 도보·버스·승용차를 나눈다. 한 리뷰는 도보 0.1~15km/h, 버스 1~120km/h, 승용차 3~180km/h 같은 속도 범위를 사용했고, 95퍼센타일 속도 기준으로 도보(3.0m/s 미만)·자전거·차량(17.4m/s 초과)·철도(27.5m/s 초과)를 구분한 연구도 있다. 순간 속도와 GPS 정확도가 가장 영향력이 큰 특징이고, **정차 비율과 가속도 변동**이 버스와 승용차를 구분하는 데 쓰인다.
  - [Travel Mode Detection with Varying Smartphone Data Collection Frequencies (Sensors 2016)](https://doi.org/10.3390/s16050716)
  - [Travel Mode Detection Based on GPS Raw Data Collected by Smartphones: A Systematic Review](https://mdpi.com/2078-2489/7/4/67/htm)
  - [Methods for Real-Time Prediction of the Mode of Travel Using Smartphone-Based GPS and Accelerometer Data](https://pmc.ncbi.nlm.nih.gov/articles/PMC5620731/)
  - [Detecting Transportation Mode Using Dense Smartphone GPS Trajectories and Transformer Models](https://arxiv.org/html/2603.00340)
- 지하철은 지하에서 GPS가 안 잡힌다. 지하 측위를 따로 다루는 연구가 있을 정도다. [SubwayPS](https://arxiv.org/pdf/1904.01675). 위치 서비스 문서도 속도는 m/s 단위로 주고, 일부 기기는 GPS 하드웨어 문제로 위치가 잘 안 올 수 있다고 안내한다. [react-native-geolocation-service 문서](https://github.com/Agontuk/react-native-geolocation-service/blob/master/docs/accuracy.md)
- 서울 지하철 평균 표정속도는 약 34km/h(노선·구간별 29~47km/h). [서울교통공사 1~8호선 표정속도](https://data.seoul.go.kr/dataList/OA-22492/L/1/datasetView.do), [정책뉴스](https://www.korea.kr/news/policyNewsView.do?newsId=65045209)

**구현에 반영한 것**
- 도보: 시간 가중 상위 5% 속도가 15km/h 이하이고 차량 속도 구간이 30% 미만일 때만 인정.
- 대중교통(버스 등): 이동 시간의 50% 이상이 12km/h 이상, 상위 5% 속도 130km/h 이하.
- 지하철: GPS가 40초 이상 끊긴 뒤 300m 이상 점프하고, 그 사이 평균 속도가 15~120km/h이며 점프 합계 1km 이상이면 인정(표정속도 34km/h 근처를 포함하는 넉넉한 범위).
- 순간 속도 200km/h 초과는 위치 오류로 거절, 이동이 끝난 지 30분이 지난 경로는 거절, 인정 거리는 앱 값이 아니라 서버가 경로로 계산.
- 앱에서 일시정지 후 재개한 지점은 이동을 관측하지 못한 구간이라 거리·점프로 세지 않는다(일시정지 중 자차 이동을 지하철 점프로 위장하는 것을 막기 위한 **내 설계**).

**주의: 위 숫자(12·15·130km/h, 40초, 300m, 1km, 50%)는 연구에서 가져온 범위를 바탕으로 제가 정한 값이고, 실제 버스·지하철 주행 데이터로 조정한 값이 아닙니다.** 실제로 걷기·버스·지하철을 타서 기록한 뒤 조정이 필요합니다.

**한계(근거 포함)**: 연구에서도 속도만으로 버스와 승용차를 가르지 못해 정차 비율·가속도 변동·노선 정보를 같이 쓴다. 이 구현은 "걷는 속도뿐인데 대중교통", "차량 속도인데 도보" 같은 명백한 불일치만 거절한다. 위치 조작 앱이나 요청을 직접 만들어 보내는 경우는 막지 못한다.

### 2-2. 텀블러 사진 인증 (제안 2)
**근거 자료**
- 주요 카페는 개인컵 사용 시 할인한다: 스타벅스 400원, 투썸플레이스 300원, 이디야 200원, 2026년 기사 기준 탄소중립포인트 300원을 더해 최대 800원. [파이낸셜뉴스](https://www.fnnews.com/news/202202161812002976), [서울경제](https://www.sedaily.com/article/13443767), [네이트 뉴스(2026)](https://m.news.nate.com/view/20260713n27643)
- 스타벅스는 개인컵 할인 대신 "에코별" 적립을 고를 수 있고, 둘은 중복되지 않는다. 그래서 영수증 문구 판정에 "에코별"도 넣었다. [서울시 미디어허브](https://mediahub.seoul.go.kr/archives/2009718)
- 정부의 탄소중립포인트도 "텀블러·다회용컵 사용"을 매장 쪽 기록(QR·전화번호)으로 확인한다. 즉 사용 사실의 가장 강한 증거는 **매장이 남긴 기록(영수증)**이다. [탄소중립포인트](https://www.cpoint.or.kr/netzero/main.do), [서울시 보도](https://news.seoul.go.kr/env/archives/557174)

**구현**: 영수증이 카페이고 24시간 안에 결제했으며, **같은 사진 안에** 텀블러와 영수증이 함께 보이면 통과(`PHOTO`). 영수증 자체에 개인컵 할인·에코별 문구가 있으면 영수증만으로도 통과(`RECEIPT_DISCOUNT`, 매장 POS 기록이라 가장 강한 증거).

**악용 방지 설계 (2026-10-04 보강)**: 처음 구현은 사진 여러 장을 합쳐서 "텀블러가 어딘가에 보이고 영수증이 어딘가에 있으면" 통과시켰다. 그러면 집에서 찍은 텀블러 사진 + 일회용 컵으로 산 진짜 영수증을 따로 내서 통과시킬 수 있다(텀블러와 구매가 연결되지 않음). 그래서 사진 한 장 한 장을 따로 평가하고 한 사진이 모든 조건을 만족해야 하도록 바꿨다. 사진은 영수증이 같이 찍혀 있으므로 결제 이후에 찍은 사진임도 함께 보장된다.
- 막는 것: 텀블러·영수증 따로 제출(`SEPARATE_PHOTOS`), 오래된 영수증(24시간 초과), 같은 영수증 재사용(지문), 미래 시각 영수증.
- 못 막는 것: 남의 영수증+내 텀블러를 한 사진에 같이 찍는 연출, 텀블러를 들고 가서 일회용 컵으로 마시는 경우, 영수증을 건네받은 경우. 사진·영수증만으로는 "정말 그 컵으로 마셨는지"를 증명할 수 없다. 가장 확실한 증거는 영수증의 개인컵 할인 줄(매장 기록)이다.
- 전자영수증(앱 화면)만 있는 사용자는 한 사진에 텀블러와 같이 찍기 어렵다(한 기기로는 화면을 찍을 수 없음). 이때는 영수증에 개인컵 할인·에코별 줄이 있어야 영수증 캡처만으로 인증된다.

### 2-3. 저탄소 인증 마크 (제안 3)
**근거 자료**: 마크는 세 가지다. 환경부 탄소성적표지(1단계 배출량 인증, 2단계 저탄소제품, 3단계 탄소중립제품), 농림축산식품부 저탄소 농축산물 인증(농산물 중심), 저탄소 축산물 인증(한우, 2023년 시행). 인증이 있다는 이유로 마트에서 흔히 보이지는 않을 수 있다는 지적도 있다.
- [탄소성적표지 제도(한국에너지공단 블로그)](http://blog.energy.or.kr/?p=12404), [저탄소 축산물 인증제도(식품나라)](https://www.foodnuri.go.kr/portal/bbs/B0000284/view.do?nttId=234057&menuNo=300089&deleteCd=0&pageIndex=1), [농식품 인증마크 13가지(정책뉴스)](https://www.korea.kr/news/healthView.do?newsId=148858575), [희미해지는 인증제(뉴스펭귄)](https://www.newspenguin.com/news/articleView.html?idxno=12123)

**구현**: 인정 마크는 `LOW_CARBON_PRODUCT`(저탄소제품), `LOW_CARBON_AGRI`, `LOW_CARBON_LIVESTOCK` 세 가지이고, 마크가 보이는 상품과 영수증이 **같은 사진**에 있어야 한다. 배출량만 표시된 1단계 표지(`CARBON_FOOTPRINT_ONLY`)와 친환경·유기농(`OTHER_ECO`)은 별도로 읽어서 "저탄소 인증이 아니에요"라고 안내한다. 마크가 드물 수 있다는 점 때문에 저탄소 챌린지의 직접 체크 한도는 텀블러보다 넉넉하게(목표의 절반 올림) 뒀다.

**미검증**: 실제 포장의 작은 마크를 Gemini가 얼마나 정확히 읽는지는 합성 이미지로 확인할 수 없어서 **실제 상품 사진으로 테스트하지 못했다.** 프롬프트와 판정 규칙은 있지만 정확도는 모른다.

### 2-4. 영수증 인증 공통: 중복 방지·위조
**근거 자료**: 영수증 재사용·중복은 거래 시각·금액·가맹점·번호 같은 항목으로 만든 해시(지문)로 잡는 것이 일반적이고, EXIF 메타데이터는 쉽게 지울 수 있어 보조 증거로만 써야 한다.
- [How to Detect Duplicate Receipts (Klippa)](https://www.klippa.com/en/blog/how-to-detect-duplicate-receipts/), [Receipt Fraud Detection (Klippa)](https://www.klippa.com/en/blog/information/detect-fake-receipts/), [Anti-fraud checks for receipt programs (Snipp)](https://www.snipp.com/blog/anti-fraud-checks-for-receipt-programs), [How to Spot Fake Receipts](https://invoicedataextraction.com/blog/detect-fake-receipts)

**구현**: 결제 시각이 읽히면 지금부터 24시간 안(미래는 15분 오차까지)의 영수증만 인정한다(시각을 못 읽으면 오늘·어제). 영수증 지문 = 날짜·시각·금액의 해시(시각을 못 읽었으면 가맹점 앞 4글자 추가), DB 유니크로 사용자·챌린지와 상관없이 한 번만 사용. 같은 영수증을 원본(PNG)과 축소·저화질(JPEG)로 찍어도 지문이 같았다. 편집 흔적이 뚜렷하면 거절, 앱 화면을 찍은 전자영수증은 정상이라 거절하지 않는다. EXIF는 쓰지 않는다.

### 2-5. 하이브리드(제안 4)
카탈로그에 `verification_methods`·`self_check_limit`을 추가. 텀블러는 목표의 절반(내림)까지만 직접 체크, 저탄소 마크는 절반(올림). 인증은 횟수 제한 없음, 같은 영수증만 불가. 인증할 때마다 보너스 +5P를 하루 3번까지만 준다 — 인증이 무제한이라 포인트 파밍(포인트는 캐릭터 레벨업 조건)을 막기 위한 **내 설계**이고 숫자는 팀 협의 대상이다.

### 2-6. 쇼핑(제안 5)
**근거 자료**: 국내 무지출 챌린지는 가계부·금융 앱 화면 캡처 같은 자기 보고식 인증이 주류다. [무지출 챌린지(더스쿠프)](https://www.thescoop.co.kr/news/articleView.html?idxno=305833) 은행·카드 연동이나 "안 샀다"의 직접 증명 사례는 찾지 못했다.
**구현**: 직접 증명은 불가능하다고 보고, 이미 앱에 등록된 영수증과의 교차 검증만 했다. 무구매 챌린지(`SHOP_2`, `SHOP_3`)는 그날 "쇼핑소비재" 영수증이 있으면 체크를 거절하고, 체크한 날에 영수증이 뒤늦게 등록되면 그 체크를 취소한다. 영수증을 안 올린 사람은 못 잡는다. `SHOP_1`(구매 미루기)은 자율 체크.

## 3. AI 비전(Gemini) 사용 근거
- Gemini API는 여러 이미지를 한 요청에 넣고, `responseSchema`로 JSON 구조를 강제할 수 있으며 영수증에서 가맹점·날짜·금액·품목을 읽는 용도로 쓰인다. [Image understanding](https://ai.google.dev/gemini-api/docs/image-understanding), [Structured output](https://firebase.google.com/docs/ai-logic/generate-structured-output)
- 설계: AI는 사진에서 보이는 **사실만 읽고**(텀블러 유무, 마크 종류, 영수증 필드), 통과 여부는 코드 규칙이 정한다. temperature 0, 같은 읽기 결과면 같은 판정.
- 실제 호출 확인(합성 영수증 5종): 개인컵 할인 영수증 통과, 할인 없는 카페 영수증은 NO_TUMBLER, 3일 지난 영수증은 TOO_OLD, 마트 영수증은 NOT_CAFE/NO_MARK. 호출당 약 1.5~2초(첫 호출은 클라이언트 준비 때문에 약 18초라 서버 기동 때 미리 준비).

## 4. 검증하지 못한 것 (사용자 확인 필요)
1. 실제 텀블러 사진을 `tumbler_visible`로 정확히 읽는지 (합성 영수증만 테스트).
2. 실제 상품의 저탄소 마크 판독 정확도.
3. 실제 도보·버스·지하철 이동에서 속도 기준값이 맞는지(합성 경로로만 검증), 휴대폰 GPS가 지하에서 실제로 어떤 패턴으로 끊기는지.
4. 앱 화면(폰)에서의 사진 촬영·업로드 동작 (Metro 번들 컴파일만 확인).
5. 영수증이 실제로 "개인컵 할인"이라고 찍히는 카페 목록 (기사 기준 브랜드별 할인액만 확인, 영수증 문구는 사례를 찾지 못해 키워드를 넓게 잡음).
