"""
=== 프로그램 설명 ===
1. 필요한 라이브러리 설치:
   이 프로그램은 아래 명령어로 설치 가능한 라이브러리를 사용합니다:
   - pip install aiohttp

2. 프로그램 종료 방법:
   - 프로그램은 SIGINT (Ctrl+C) 또는 SIGTERM 신호를 받으면 실행 중 작업을 정리하고 안전하게 종료됩니다.
   - 종료 버튼(Ctrl+C)을 누르면 수집 중인 작업이 순차적으로 완료된 후 종료됩니다.

3. 데이터 수집 로직:
   - 10분마다 모든 기기의 메타데이터를 업데이트합니다.
   - 12초마다 모든 기기의 상태를 병렬로 조회합니다.
   - 수집된 데이터는 YYYYMMDD 형식의 날짜 기반 폴더 안에 CSV 파일로 저장되며, 데이터는 누적됩니다.
   - 조회 중 필드 누락 또는 오류가 발생한 기기는 "밴 목록"에 추가되어 이후 조회에서 제외됩니다.
"""

import os
import csv
import json
import aiohttp
import asyncio
import logging
import signal
from datetime import datetime

# === 로깅 설정 === #
LOG_FILE = os.path.abspath("C:/smartthings_data/logs/smartthings.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# === 변수 설정 === #
API_TOKEN = "23d3c61f-824d-4e54-802d-1b8d7f8164b7"  # 스마트싱스 API 토큰
API_BASE_URL = "https://api.smartthings.com/v1"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

# 절대경로 설정
CSV_BASE_DIR = os.path.abspath("C:/smartthings_data/csv_data")  # CSV 파일 저장 기본 디렉토리
BACKUP_DIR = os.path.abspath("C:/smartthings_data/backup_data")  # 백업 파일 저장 경로
METADATA_FILE = os.path.abspath("C:/smartthings_data/metadata/device_metadata.json")  # 단일 메타데이터 파일 경로
BAN_LIST_FILE = os.path.abspath("C:/smartthings_data/ban_list.json")  # 밴 목록 파일 경로
os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)

# 시간 설정
START_TIME = "08:00"  # 수집 시작 시간 (한국 시간 기준)
END_TIME = "23:00"  # 수집 종료 시간 (한국 시간 기준)

# 10분에 한 번 기기 정보 업데이트
DEVICE_UPDATE_INTERVAL = 600  # 초 단위
# 12초마다 기기 상태 조회
DEVICE_STATUS_INTERVAL = 12  # 초 단위
# 1시간마다 백업
BACKUP_INTERVAL = 3600  # 초 단위

# === 로컬 관리 데이터 === #
device_metadata = []  # {id, label, location_name} 저장
ban_list = []  # 밴된 기기 목록
running = True  # 프로그램 실행 상태
current_date = datetime.now().strftime("%Y%m%d")  # 현재 날짜
last_update_time = None  # 마지막 기기 목록 업데이트 시간

# === 함수 정의 === #
def load_metadata():
    """메타데이터를 단일 파일에서 로드합니다."""
    global device_metadata
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, "r", encoding="utf-8") as file:
            device_metadata = json.load(file)
        logging.info(f"메타데이터를 파일에서 로드했습니다. ({len(device_metadata)}개 기기)")
    else:
        logging.warning("메타데이터 파일이 없습니다. 초기 업데이트가 필요합니다.")


def save_metadata():
    """현재 메타데이터를 단일 파일로 저장합니다."""
    global device_metadata
    with open(METADATA_FILE, "w", encoding="utf-8") as file:
        json.dump(device_metadata, file, ensure_ascii=False, indent=4)
    logging.info(f"메타데이터를 파일로 저장했습니다: {METADATA_FILE}")


def load_ban_list():
    """밴 목록을 로드합니다."""
    global ban_list
    if os.path.exists(BAN_LIST_FILE):
        with open(BAN_LIST_FILE, "r", encoding="utf-8") as file:
            ban_list = json.load(file)
        logging.info(f"밴 목록을 로드했습니다. ({len(ban_list)}개 기기)")
    else:
        logging.info("밴 목록 파일이 없습니다. 초기화된 상태로 시작합니다.")


def save_ban_list():
    """밴 목록을 저장합니다."""
    global ban_list
    with open(BAN_LIST_FILE, "w", encoding="utf-8") as file:
        json.dump(ban_list, file, ensure_ascii=False, indent=4)
    logging.info(f"밴 목록을 파일로 저장했습니다. ({len(ban_list)}개 기기)")


def save_to_csv(device_status, device_id):
    """상태 데이터를 날짜 기반 폴더에 CSV로 저장하고, 저장된 상태를 로깅합니다."""
    global current_date

    # 현재 날짜 폴더 경로
    today_date = datetime.now().strftime("%Y%m%d")
    folder_path = os.path.join(CSV_BASE_DIR, today_date)

    # 날짜 변경 시 폴더 생성
    if today_date != current_date or not os.path.exists(folder_path):
        current_date = today_date
        os.makedirs(folder_path, exist_ok=True)  # 폴더가 없으면 생성

    # 파일 저장 경로
    filename = f"{device_status['label']}_{device_id}_{today_date}.csv"
    filepath = os.path.join(folder_path, filename)

    file_exists = os.path.isfile(filepath)
    try:
        with open(filepath, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Timestamp", "Label", "Location Name", "Power (W)", "Energy (Wh)"])
            writer.writerow([
                device_status["timestamp"],  # 시분초까지 포함된 타임스탬프
                device_status["label"],
                device_status["location_name"],
                device_status["power"],
                device_status["energy"]
            ])
        # 성공적으로 저장된 상태를 로깅
        logging.info(
            f"CSV에 저장됨: Label={device_status['label']}, Power={device_status['power']}W, "
            f"Energy={device_status['energy']}Wh, ID={device_id}"
        )
    except Exception as e:
        logging.error(f"CSV 저장 중 오류 발생: {e}")



async def fetch_device_list(session):
    """API에서 모든 기기의 기본 정보를 가져와 로컬 관리 데이터를 업데이트합니다."""
    global device_metadata, ban_list
    async with session.get(f"{API_BASE_URL}/devices", headers=HEADERS) as response:
        if response.status == 200:
            devices = await response.json()
            metadata = []
            for device in devices.get("items", []):
                label = device["label"]
                device_id = device["deviceId"]
                if label.startswith("SMP"):
                    # 라벨 이름이 SMP로 시작하는 경우만 추가
                    location_name = await fetch_location_name(session, device["locationId"])
                    metadata.append({
                        "id": device_id,
                        "label": label,
                        "location_name": location_name
                    })
                else:
                    # 조건에 맞지 않는 기기는 밴 목록에 추가
                    if device_id not in ban_list:
                        ban_list.append(device_id)
                        logging.warning(f"밴 목록에 추가된 기기: {label} (ID={device_id})")
            device_metadata = metadata
            save_ban_list()  # 밴 목록 저장
            logging.info(f"기기 목록 업데이트 완료. 총 {len(device_metadata)}개 기기.")
            save_metadata()
        else:
            logging.error(f"기기 목록 업데이트 실패: {response.status}")


async def fetch_location_name(session, location_id):
    """API에서 위치 이름을 가져옵니다."""
    async with session.get(f"{API_BASE_URL}/locations/{location_id}", headers=HEADERS) as response:
        if response.status == 200:
            location = await response.json()
            return location.get("name", "Unknown")
        return "Unknown"


async def fetch_device_status(session, device):
    """API에서 특정 기기의 상태 정보를 가져오고 데이터를 CSV에 저장합니다."""
    device_id = device["id"]
    if device_id in ban_list:
        logging.warning(f"밴된 기기입니다. 상태 조회를 건너뜁니다: {device_id}")
        return None

    try:
        async with session.get(f"{API_BASE_URL}/devices/{device_id}/status", headers=HEADERS) as response:
            if response.status == 200:
                data = await response.json()
                components = data.get("components", {})
                main = components.get("main", {})
                power = main.get("powerMeter", {}).get("power", {}).get("value")
                energy = main.get("energyMeter", {}).get("energy", {}).get("value")

                if power is None or energy is None:
                    raise KeyError("필드 누락")

                device_status = {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 시분초 포함 타임스탬프
                    "label": device["label"],
                    "location_name": device["location_name"],
                    "power": power,
                    "energy": energy
                }

                # CSV에 저장 및 상태 로깅
                save_to_csv(device_status, device_id)
                return device_status
            else:
                logging.error(f"기기 상태 조회 실패: {device_id} (응답 코드 {response.status})")
    except KeyError as e:
        logging.error(f"기기 상태 조회 중 필드 누락 발생: {device_id}, 이유: {str(e)}")
        ban_list.append(device_id)
        save_ban_list()

    return None


async def periodic_tasks(session):
    """주기적으로 실행되는 작업"""
    global last_update_time  # 마지막 업데이트 시간을 추적
    last_update_time = None  # 초기값 설정

    while running:
        # 현재 시간 기록
        start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f"\n================= 시간: {start_time} =================")

        # 10분마다 기기 목록 업데이트
        current_time = datetime.now()
        if current_time.minute % 10 == 0 and (last_update_time is None or last_update_time.minute != current_time.minute):
            last_update_time = current_time
            await fetch_device_list(session)
            logging.info("기기 목록이 업데이트되었습니다.")

        # 모든 기기의 상태를 병렬로 조회
        tasks = [fetch_device_status(session, device) for device in device_metadata]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 결과 처리 (필요한 경우 로깅)
        for result, device in zip(results, device_metadata):
            if isinstance(result, Exception):
                logging.error(f"기기 {device['label']} 상태 조회 중 오류 발생: {result}")

        # 비동기 요청 완료 시간 기록
        end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f"\n================= 완료: {end_time} =================\n")

        # 12초 대기
        await asyncio.sleep(DEVICE_STATUS_INTERVAL)


async def scheduler(use_time_mode=True):
    """스케줄링된 작업 실행"""
    global running

    if not os.path.exists(CSV_BASE_DIR):
        os.makedirs(CSV_BASE_DIR)

    async with aiohttp.ClientSession() as session:
        if not device_metadata:
            logging.info("메타데이터가 비어 있습니다. 초기 업데이트를 시작합니다.")
            await fetch_device_list(session)

        while running:
            current_time = datetime.now().strftime("%H:%M")
            if use_time_mode:
                if START_TIME <= current_time <= END_TIME:
                    await periodic_tasks(session)
                else:
                    logging.info(f"수집 시간대가 아닙니다. ({current_time}) 대기 중...")
                    await asyncio.sleep(60)
            else:
                await periodic_tasks(session)

        logging.info("스케줄러가 안전하게 종료되었습니다.")





def safe_input(prompt):
    """안전한 input 호출 (종료 요청 시 예외 처리 포함)"""
    try:
        return input(prompt)
    except (KeyboardInterrupt, UnicodeDecodeError):
        logging.info("프로그램 종료 요청이 감지되었습니다. 종료합니다.")
        global running
        running = False
        return None


def print_device_list():
    """현재 저장된 기기 메타데이터를 출력합니다."""
    if not device_metadata:
        logging.warning("메타 데이터 내 기기 목록이 비어 있습니다.")
    else:
        logging.info("\n=== 현재 기기 목록 ===")
        for idx, device in enumerate(device_metadata, start=1):
            logging.info(f"{idx}. ID: {device['id']}, Label={device['label']}, Location={device['location_name']}")
        logging.info("======================\n")


def shutdown_handler(signum, frame):
    """프로그램 종료 요청 처리"""
    global running
    logging.info("프로그램 종료 요청이 감지되었습니다. 정리 중...")
    running = False

    try:
        # 현재 실행 중인 이벤트 루프 가져오기
        loop = asyncio.get_running_loop()
        loop.stop()  # 이벤트 루프 중지
    except RuntimeError:
        logging.warning("이벤트 루프가 이미 중지된 상태입니다.")


def reset_event_loop():
    """이벤트 루프가 닫힌 상태를 재설정합니다 (Windows 환경)"""
    try:
        asyncio.get_running_loop().close()
    except RuntimeError:
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info("스마트띵스 데이터 수집을 시작합니다.")
    load_metadata()
    load_ban_list()
    print_device_list()

    try:
        mode = safe_input("모드를 선택하세요 (1: 시간대 설정 모드, 2: 즉시 수집 모드): ")
        if mode == "1":
            logging.info("시간대 설정 모드로 실행합니다.")
            asyncio.run(scheduler(use_time_mode=True))
        elif mode == "2":
            logging.info("즉시 수집 모드로 실행합니다.")
            asyncio.run(scheduler(use_time_mode=False))
        else:
            if mode is not None:
                logging.error("잘못된 입력입니다. 프로그램을 종료합니다.")
    finally:
        # 종료 시 잔여 작업 정리
        try:
            asyncio.run(asyncio.sleep(0))  # 잔여 비동기 작업 처리
        except RuntimeError as e:
            logging.info(f"잔여 작업 정리 중 오류 발생 (무시됨): {e}")

        # Windows 환경에서는 명시적으로 이벤트 루프 재설정
        reset_event_loop()
        logging.info("프로그램이 정상적으로 종료되었습니다.")
