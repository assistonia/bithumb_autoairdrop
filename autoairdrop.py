import os
import jwt 
import uuid
import hashlib
import time
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv
import json
import base64
import decimal # 소수점 정밀 계산을 위해 추가

# Load environment variables
load_dotenv()

# ==============================================================================
# 사용자 설정 구간
# ==============================================================================
# 거래할 코인 티커 리스트 (예: ['FLOCK', 'INIT', 'XTER'])
# 여기에 원하는 코인 심볼을 대문자로 추가하거나 수정하세요.
COIN_TICKERS = ['FLOCK', 'INIT', 'XTER']

#
#flock init 0530 ~ 0601 xter 0529 ~ 0601

TRADE_AMOUNT = 6000  # 각 코인별 거래 금액 (원)
# TARGET_PROFIT_KRW = 10 # 테스트용 최소 익절/손절 금액 (실제 사용 시 조정 필요) # 현재 미사용

ORDER_CHECK_DELAY_SECONDS = 5 # 매수 후 체결내역 확인까지 대기 시간
ORDER_FILL_TIMEOUT_SECONDS = 30 # 체결 내역을 기다리는 최대 시간
ORDER_CHECK_INTERVAL_SECONDS = 5 # 체결 내역 확인 재시도 간격

# API 키 설정 (자동으로 .env 파일에서 로드됩니다)
accessKey = os.getenv('BITHUMB_ACCESS_KEY')
# .env 파일의 SECRET_KEY가 base64 인코딩 되어있다면 아래 b64decode 사용
# secretKey = base64.b64decode(os.getenv('BITHUMB_SECRET_KEY')).decode()
# 그렇지 않고 평문이라면 아래 코드 사용 (현재 설정)
secretKey = os.getenv('BITHUMB_SECRET_KEY') 
# ==============================================================================

apiUrl = 'https://api.bithumb.com'
publicApiUrl = 'https://api.bithumb.com/public' # Public API 용 URL

# Decimal 컨텍스트 설정 (빗썸 최소 주문 단위 등에 맞춰 조정 필요)
# 예시: 소수점 8자리까지, 반올림 방식은 ROUND_HALF_UP
ctx = decimal.Context()
ctx.prec = 20 # 충분한 정밀도
ctx.rounding = decimal.ROUND_HALF_UP 


print(f"ACCESS_KEY: {accessKey[:10] if accessKey else '없음'}...")
print(f"SECRET_KEY: {secretKey[:10] if secretKey else '없음'}...")
print(f"거래 대상 티커: {COIN_TICKERS}")
print(f"각 티커별 거래 금액: {TRADE_AMOUNT}원")

def get_current_price(coin_ticker):
    """지정된 코인의 현재가를 조회합니다. (Public API)"""
    endpoint = f'/ticker/{coin_ticker.upper()}_KRW'
    try:
        response = requests.get(publicApiUrl + endpoint)
        response.raise_for_status() # 오류 발생 시 예외 처리
        data = response.json()
        if data.get("status") == "0000" and data.get("data", {}).get("closing_price"):
            current_price = data["data"]["closing_price"]
            print(f"현재가 조회 ({coin_ticker}): {current_price} KRW")
            return decimal.Decimal(current_price) # Decimal 타입으로 반환
        else:
            print(f"{coin_ticker} 현재가 조회 실패: {data.get('message', '알 수 없는 오류')}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"{coin_ticker} 현재가 조회 중 네트워크 오류 발생: {e}")
        return None
    except Exception as e:
        print(f"{coin_ticker} 현재가 조회 중 오류 발생: {e}")
        return None

def get_order_details(order_id_str):
    """지정된 order_id(uuid)의 주문 상세 정보를 조회하여 실제 체결 수량을 반환합니다."""
    # 빗썸 API 문서 기준: GET /v1/order?uuid={order_id}
    endpoint_path = '/v1/order' 
    
    query_params = {'uuid': order_id_str} 
    query_string_for_hash_bytes = urlencode(query_params).encode('utf-8')
    query_string_for_request = urlencode(query_params) 
    
    hash_obj = hashlib.sha512()
    hash_obj.update(query_string_for_hash_bytes)
    query_hash_val = hash_obj.hexdigest()
    
    payload = {
        'access_key': accessKey,
        'nonce': str(uuid.uuid4()),
        'timestamp': round(time.time() * 1000),
        'query_hash': query_hash_val,
        'query_hash_alg': 'SHA512',
    }
    
    try:
        jwt_token = jwt.encode(payload, secretKey, algorithm='HS256')
    except Exception as e:
        print(f"JWT 생성 오류 (주문 상세 조회 - {order_id_str}): {e}")
        return None, "error" 
        
    authorization_token = f'Bearer {jwt_token}'
    headers = {
        'Authorization': authorization_token,
    }

    full_url = f"{apiUrl}{endpoint_path}?{query_string_for_request}"

    try:
        print(f"주문 상세 조회 요청 ({order_id_str}): GET {full_url}")
        response = requests.get(full_url, headers=headers)
        print(f"주문 상세 조회 응답 ({order_id_str}): {response.status_code}")
        response_data = response.json()

        # /v1/order 응답 구조 확인 및 수정:
        # 로그를 보면 status_code == 200 일 때, response_data에 'status' 최상위 키가 없고
        # 바로 주문 상세 정보가 포함된 것으로 보임. 또한 'data' 키로 감싸여 있지도 않음.
        # 성공적인 응답의 예: {'uuid': '...', 'state': 'done', ...}
        if response.status_code == 200: 
            # response_data 자체가 주문 정보를 담고 있는 단일 객체로 가정
            order_info = response_data 
            
            if order_info and isinstance(order_info, dict) and order_info.get('uuid') == order_id_str:
                order_state = order_info.get("state")
                executed_volume_str = order_info.get("executed_volume", "0")
                executed_volume = decimal.Decimal(executed_volume_str)
                
                print(f"주문 ID {order_id_str}: 상태='{order_state}', 체결수량='{executed_volume}'")
                
                if order_state == 'done': 
                    return executed_volume, order_state
                elif order_state == 'wait' or order_state == 'placed': 
                    print(f"주문 {order_id_str}이(가) 미체결 또는 부분 체결 상태: {order_state}. 현재 체결량: {executed_volume}")
                    return executed_volume, order_state 
                else: 
                    print(f"주문 {order_id_str}이(가) 체결되지 않은 상태입니다: {order_state}")
                    return decimal.Decimal("0"), order_state
            else:
                # 주문 정보를 찾을 수 없거나 uuid 불일치
                print(f"주문 {order_id_str} 상세 정보 파싱 실패 또는 UUID 불일치. 응답: {order_info}")
                return None, "error"
        else:
            # HTTP 에러 또는 빗썸 자체 에러 (status 키가 있는 경우 등)
            error_message = response_data.get('message') # 빗썸 자체 에러 메시지 시도
            if not error_message and isinstance(response_data.get('error'), dict):
                 error_message = response_data.get('error').get('message')
            if not error_message:
                error_message = str(response_data) # 최후의 수단

            print(f"주문 {order_id_str} 상세 조회 API 오류 (code: {response.status_code}): {error_message}")
            return None, "error"
            
    except Exception as e:
        print(f"주문 {order_id_str} 상세 조회 중 오류 발생: {e}")
        return None, "error"

# get_coin_balance 함수는 현재 사용하지 않으므로 주석 처리 또는 삭제 가능
# def get_coin_balance(coin_ticker):
#     """지정된 코인의 사용 가능한 잔고를 조회합니다."""
# ... (이하 get_coin_balance 함수 내용) ...

def market_buy(current_coin_ticker):
    requestBody = dict(
        market=f'KRW-{current_coin_ticker.upper()}', 
        side='bid', 
        order_type='price', 
        price=str(TRADE_AMOUNT) 
    )
    
    print(f"매수 요청 Body ({current_coin_ticker}): {requestBody}")

    query = urlencode(requestBody).encode('utf-8')
    hash_obj = hashlib.sha512()
    hash_obj.update(query)
    query_hash = hash_obj.hexdigest()
    
    payload = {
        'access_key': accessKey,
        'nonce': str(uuid.uuid4()),
        'timestamp': round(time.time() * 1000), 
        'query_hash': query_hash,
        'query_hash_alg': 'SHA512',
    }   
    
    try:
        jwt_token = jwt.encode(payload, secretKey, algorithm='HS256')
    except Exception as e:
        print(f"JWT 생성 오류 (매수 - {current_coin_ticker}): {e}")
        return None
        
    authorization_token = f'Bearer {jwt_token}'
    headers = {
      'Authorization': authorization_token,
      'Content-Type': 'application/json'
    }

    try:
        response = requests.post(apiUrl + '/v2/orders', data=json.dumps(requestBody), headers=headers)
        print(f"매수 응답 ({current_coin_ticker}): {response.status_code}")
        response_json = response.json()
        print(response_json)
        # /v2/orders POST 응답에서 order_id는 최상위 키로 존재 (문서 기준)
        # 이전 코드에서는 response_json.get('order_id') 였으나, 빗썸 v2 문서 예시 응답은 {'id': '...', ...} 또는 {'order_id': ...}
        # 실제 응답 로그에서 'order_id'로 왔었음.
        if response.status_code == 201 and response_json.get('order_id'):
             return response_json.get('order_id')
        else:
            print(f"매수 주문 실패 응답 ({current_coin_ticker}): {response_json}")
            return None
    except Exception as err:
        print(f"매수 중 오류 발생 ({current_coin_ticker}): {err}")
        return None

def market_sell(current_coin_ticker, volume_to_sell):
    if not isinstance(volume_to_sell, decimal.Decimal) or volume_to_sell <= decimal.Decimal("0"):
        print(f"매도할 수량이 유효하지 않습니다 ({current_coin_ticker}): {volume_to_sell} (타입: {type(volume_to_sell)})")
        return None

    # 빗썸 API에서 요구하는 volume 형식에 맞게 변환 (코인별 최소 주문 수량 및 소수점 정책 확인 필요)
    # 예시: XTER의 경우 소수점 4자리까지 허용 가정
    # Decimal을 문자열로 변환 시, quantize를 사용하여 자릿수 제어
    # 예를 들어, XTER가 소수점 4자리까지 지원한다면:
    try:
        # 코인별 최소 주문 수량 및 소수점 자릿수에 대한 정보가 없으므로, 일단 8자리로 시도.
        # 이 부분은 실제 코인별 정책에 맞게 조정 필요.
        # volume_str = f"{volume_to_sell.quantize(decimal.Decimal('0.00000001'), rounding=decimal.ROUND_DOWN)}" 
        # 이전 코드에서는 .4f (소수점 4자리)를 사용했었음. XTER의 경우 4자리가 맞다면 아래와 같이.
        # 대부분의 코인은 4자리 또는 그 이하를 지원하므로, 일단 4자리로.
        volume_str = f"{volume_to_sell.quantize(decimal.Decimal('0.0001'), rounding=decimal.ROUND_DOWN)}"
        if decimal.Decimal(volume_str) <= decimal.Decimal("0"): # 포맷팅 후 0이하가 되면 안됨
             print(f"매도할 수량이 포맷팅 후 0 이하가 되었습니다 ({current_coin_ticker}): {volume_str}")
             return None
    except Exception as e:
        print(f"매도 수량 문자열 변환 중 오류 ({current_coin_ticker}): {e}")
        return None

    requestBody = dict(
        market=f'KRW-{current_coin_ticker.upper()}', 
        side='ask', 
        order_type='market',
        volume=volume_str 
    )
    
    print(f"매도 요청 Body ({current_coin_ticker}): {requestBody}")

    query = urlencode(requestBody).encode('utf-8')
    hash_obj = hashlib.sha512()
    hash_obj.update(query)
    query_hash = hash_obj.hexdigest()
    
    payload = {
        'access_key': accessKey,
        'nonce': str(uuid.uuid4()),
        'timestamp': round(time.time() * 1000), 
        'query_hash': query_hash,
        'query_hash_alg': 'SHA512',
    }   
    
    try:
        jwt_token = jwt.encode(payload, secretKey, algorithm='HS256')
    except Exception as e:
        print(f"JWT 생성 오류 (매도 - {current_coin_ticker}): {e}")
        return None
        
    authorization_token = f'Bearer {jwt_token}'
    headers = {
      'Authorization': authorization_token,
      'Content-Type': 'application/json'
    }

    try:
        response = requests.post(apiUrl + '/v2/orders', data=json.dumps(requestBody), headers=headers)
        print(f"매도 응답 ({current_coin_ticker}): {response.status_code}")
        response_json = response.json()
        print(response_json)
        if response.status_code == 201 and response_json.get('order_id'):
            return response_json.get('order_id')
        else:
            print(f"매도 주문 실패 응답 ({current_coin_ticker}): {response_json}")
            return None
    except Exception as err:
        print(f"매도 중 오류 발생 ({current_coin_ticker}): {err}")
        return None

if __name__ == "__main__":
    if not accessKey or not secretKey:
        print("API 키가 .env 파일에 설정되지 않았습니다. 확인 후 다시 실행해주세요.")
        exit(1)

    for ticker in COIN_TICKERS:
        print(f"\n{'='*10} {ticker.upper()} 코인 거래 시작 {'='*10}")

        # --- 현재가 조회 (매수/매도 판단에 직접 사용되지는 않지만 참고용으로 둘 수 있음) ---
        # current_price = get_current_price(ticker)
        # if not current_price or current_price <= decimal.Decimal("0"):
        #     print(f"{ticker} 현재가를 가져올 수 없어 거래를 진행할 수 없습니다.")
        #     # ... (생략: 다음 티커로 넘어가는 로직)
        #     continue
        #
        # # 목표 매수 수량 계산은 이제 불필요 (TRADE_AMOUNT 만큼 매수 시도 후 실제 체결량 기준 매도)
        # calculated_target_volume = (decimal.Decimal(str(TRADE_AMOUNT)) / current_price).quantize(
        #     decimal.Decimal('0.0001'), rounding=decimal.ROUND_DOWN
        # )
        # print(f"참고용 계산된 목표 거래 수량 ({ticker}): {calculated_target_volume} (현재가: {current_price} KRW 기준, {TRADE_AMOUNT}원)")


        # --- 매수 시도 ---
        print(f"\n[{ticker.upper()}] {TRADE_AMOUNT}원 시장가 매수를 시도합니다.")
        buy_order_id = market_buy(ticker)

        if buy_order_id:
            print(f"매수 주문 요청 성공 ({ticker}): Order ID {buy_order_id}")
            print(f"약 {ORDER_CHECK_DELAY_SECONDS}초 후 매수 주문 체결 상태를 확인합니다...")
            time.sleep(ORDER_CHECK_DELAY_SECONDS)

            actual_bought_volume = decimal.Decimal("0")
            order_fully_filled = False
            
            start_time = time.time()
            while time.time() - start_time < ORDER_FILL_TIMEOUT_SECONDS:
                executed_volume_from_api, order_state = get_order_details(buy_order_id)
                
                if executed_volume_from_api is not None: # API 호출 성공 시
                    actual_bought_volume = executed_volume_from_api # 현재까지 체결된 총량
                    # API 문서에서 'done'이 전체 체결, 'wait'/'placed'가 미체결/부분체결 가능성
                    if order_state == 'done':
                        print(f"매수 주문 ({buy_order_id}) 전체 체결 확인. 실제 매수 수량: {actual_bought_volume} {ticker}")
                        order_fully_filled = True
                        break 
                    elif order_state in ['wait', 'placed']:
                        # executed_volume_from_api가 0보다 크면 부분 체결로 간주 가능
                        if executed_volume_from_api > decimal.Decimal("0"):
                            print(f"매수 주문 ({buy_order_id}) 부분 체결 상태. 현재까지 체결 수량: {actual_bought_volume} {ticker}. 잠시 후 다시 확인합니다.")
                        else:
                            print(f"매수 주문 ({buy_order_id}) 미체결 상태. 잠시 후 다시 확인합니다.")
                    elif order_state == 'error': # get_order_details에서 API 오류 발생 시
                        print(f"매수 주문 ({buy_order_id}) 상세 정보 조회 중 API 오류 발생. 잠시 후 다시 시도합니다.")
                    else: # 'cancel' 등 기타 예상치 못한 상태
                        print(f"매수 주문 ({buy_order_id})이(가) 체결되지 않거나 문제 발생: {order_state}. 매도를 진행하지 않습니다.")
                        order_fully_filled = False # 명시적으로 실패 처리
                        break 
                else: # API 호출 실패 시
                    print(f"매수 주문 ({buy_order_id}) 상세 정보 조회 실패. 잠시 후 다시 시도합니다.")
                
                if time.time() - start_time + ORDER_CHECK_INTERVAL_SECONDS < ORDER_FILL_TIMEOUT_SECONDS:
                    print(f"{ORDER_CHECK_INTERVAL_SECONDS}초 후 다시 확인...")
                    time.sleep(ORDER_CHECK_INTERVAL_SECONDS)
                else: # 타임아웃 직전 마지막 시도 후 종료
                    if not order_fully_filled:
                         print(f"매수 주문 ({buy_order_id}) 체결 확인 시간 초과. 현재까지 체결된 수량 {actual_bought_volume} {ticker} 기준으로 매도 시도.")
                    break
            
            if actual_bought_volume > decimal.Decimal("0"):
                print(f"\n[{ticker.upper()}] 실제 매수된 수량({actual_bought_volume} {ticker}) 전체 시장가 매도를 시도합니다.")
                sell_order_id = market_sell(ticker, actual_bought_volume)
                if sell_order_id:
                    print(f"매도 주문 요청 성공 ({ticker}): Order ID {sell_order_id}")
                else:
                    print(f"매도 주문 요청 실패 ({ticker}).")
            else:
                print(f"{ticker} 실제 매수된 수량이 없어 매도를 진행하지 않습니다.")
        else:
            print(f"매수 주문 요청 실패 ({ticker}).")
        
        print(f"{'='*10} {ticker.upper()} 코인 거래 종료 {'='*10}")
        
        if ticker != COIN_TICKERS[-1]:
            print("\n다음 티커 거래까지 5초 대기합니다...")
            time.sleep(5)
    
    print("\n모든 티커에 대한 거래 시도가 완료되었습니다.")
