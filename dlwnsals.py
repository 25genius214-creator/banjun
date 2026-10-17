import os
import html
import math
import random
import re
import hashlib
import json
from datetime import time as trip_clock_time
import requests
import pandas as pd
import streamlit as st
import folium
import plotly.express as px
import plotly.graph_objects as go

from streamlit_folium import st_folium


# ============================================================
# 1. 기본 설정
# ============================================================

st.set_page_config(
    page_title="VillageON | 전국 지역 활성화 플랫폼",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# 2. API KEY 안전하게 읽기
# ============================================================

def load_secret_safely(name):
    value = ""

    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""

    if not value:
        value = os.environ.get(name, "")

    return str(value).strip()


# ============================================================
# 3. 전국 시도 기본 좌표
# ============================================================

SIDO_COORDS = {
    "서울특별시": (37.5665, 126.9780),
    "부산광역시": (35.1796, 129.0756),
    "대구광역시": (35.8714, 128.6014),
    "인천광역시": (37.4563, 126.7052),
    "광주광역시": (35.1595, 126.8526),
    "대전광역시": (36.3504, 127.3845),
    "울산광역시": (35.5384, 129.3114),
    "세종특별자치시": (36.4800, 127.2890),
    "경기도": (37.4138, 127.5183),
    "강원특별자치도": (37.8228, 128.1555),
    "충청북도": (36.8000, 127.7000),
    "충청남도": (36.5184, 126.8000),
    "전북특별자치도": (35.7175, 127.1530),
    "전라남도": (34.8679, 126.9910),
    "경상북도": (36.4919, 128.8889),
    "경상남도": (35.4606, 128.2132),
    "제주특별자치도": (33.4890, 126.4983),
}


# ============================================================
# 4. 카카오 카테고리 코드
# ============================================================

CATEGORY_CODES = {
    "맛집": "FD6",
    "카페": "CE7",
    "관광지": "AT4",
    "숙박": "AD5",
    "학교": "SC4",
    "공원": "AT4",
}


CATEGORY_ICONS = {
    "맛집": "🍚",
    "카페": "☕",
    "관광지": "🏞️",
    "숙박": "🏨",
    "학교": "🏫",
    "축제": "🎉",
    "공원": "🌳",
    "문화": "🎭",
    "체험": "🎨",
}


# ============================================================
# 5. 행정안전부 API
# ============================================================

ADMIN_URL = (
    "https://apis.data.go.kr/1741000/"
    "StanReginCd/getStanReginCdList"
)


# ============================================================
# 6. 행정안전부 전국 행정구역 데이터
# ============================================================



class AdminRegionLoadError(ValueError):
    """전국 목록을 끝까지 검증하지 못했을 때 표시할, 키가 없는 오류."""


_ADMIN_COLUMNS = [
    "region_code", "sido_code", "sigungu_code", "eup_code", "li_code",
    "region_name", "order", "remark", "high_code", "low_name", "created",
]
_REGION_COLUMNS = ["sido", "sigungu", "eupmyeondong", "region_code"]
_ADMIN_SIDO_ALIASES = {
    "강원도": "강원특별자치도",
    "전라북도": "전북특별자치도",
}


def _admin_text(value):
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _admin_field(row, *names):
    for name in names:
        value = _admin_text(row.get(name))
        if value:
            return value
    return ""


def _admin_code(value, width=10):
    text = _admin_text(value)
    if re.fullmatch(r"[0-9]+\.0+", text):
        text = text.split(".", 1)[0]
    if not text.isascii() or not text.isdigit() or len(text) > width:
        return ""
    # 법정동 코드는 10자리 전체가 필요합니다. 하위 구분 코드는 0을 보충합니다.
    if width == 10 and len(text) != width:
        return ""
    return text.zfill(width)


def _admin_total(value):
    text = _admin_text(value).replace(",", "")
    if not re.fullmatch(r"[0-9]+", text):
        raise AdminRegionLoadError(
            "행정안전부 API의 전체 건수(totalCount)가 올바르지 않습니다. 잠시 후 다시 불러와 주세요."
        )
    return int(text)


def _admin_metadata(node, depth=0):
    """대량의 row 배열은 건너뛰고 head/header의 건수와 처리 코드를 읽습니다."""
    totals, codes = [], []
    if depth > 8:
        raise AdminRegionLoadError("행정안전부 API의 응답 구조를 확인할 수 없습니다.")
    if isinstance(node, list):
        for item in node:
            child_totals, child_codes = _admin_metadata(item, depth + 1)
            totals.extend(child_totals)
            codes.extend(child_codes)
    elif isinstance(node, dict):
        for key, value in node.items():
            normalized_key = str(key).lower().replace("_", "")
            if normalized_key in {"totalcount", "listtotalcount"}:
                totals.append(_admin_total(value))
            elif normalized_key == "resultcode":
                code = _admin_text(value).upper()
                if code:
                    codes.append(code)
            elif normalized_key not in {"row", "rows", "item", "items"}:
                if isinstance(value, (dict, list)):
                    child_totals, child_codes = _admin_metadata(value, depth + 1)
                    totals.extend(child_totals)
                    codes.extend(child_codes)
    return totals, codes


def _admin_row_values(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if not value:
            return []
        for key in ("item", "row", "rows"):
            if key in value:
                return _admin_row_values(value[key])
        return [value]
    raise AdminRegionLoadError(
        "행정안전부 API의 지역 목록 형식이 올바르지 않습니다. 전체 목록을 다시 불러와 주세요."
    )


def _admin_find_rows(node, depth=0):
    if depth > 8:
        raise AdminRegionLoadError("행정안전부 API의 응답 구조를 확인할 수 없습니다.")
    if isinstance(node, list):
        found = []
        present = False
        for item in node:
            result = _admin_find_rows(item, depth + 1)
            if result is not None:
                present = True
                found.extend(result)
        return found if present else None
    if isinstance(node, dict):
        for key in ("row", "rows", "items", "item"):
            if key in node:
                return _admin_row_values(node[key])
        for key in ("StanReginCd", "response", "body", "data", "result"):
            if key in node:
                result = _admin_find_rows(node[key], depth + 1)
                if result is not None:
                    return result
    return None


def _admin_decode_payload(payload):
    if not isinstance(payload, dict):
        raise AdminRegionLoadError("행정안전부 API가 올바른 JSON 객체를 반환하지 않았습니다.")
    totals, codes = _admin_metadata(payload)
    success_codes = {"INFO-0", "INFO-000", "00", "0000", "0", "200", "SUCCESS", "OK"}
    no_data_codes = {"INFO-200", "03"}
    if any(code not in success_codes | no_data_codes for code in codes):
        # API 응답 원문에는 인증키가 포함될 수 있어 메시지나 URL을 출력하지 않습니다.
        raise AdminRegionLoadError(
            "행정안전부 API가 요청을 정상 처리하지 못했습니다. 해당 API의 활용신청 승인, "
            "Decoding 인증키, 일일 호출 한도를 확인해 주세요."
        )
    rows = _admin_find_rows(payload)
    if not totals:
        if codes and any(code in no_data_codes for code in codes) and not rows:
            return [], 0
        raise AdminRegionLoadError(
            "행정안전부 API 응답에 전체 건수(totalCount)가 없어 전국 목록의 완전성을 확인할 수 없습니다."
        )
    if len(set(totals)) != 1:
        raise AdminRegionLoadError("행정안전부 API 응답의 전체 건수가 서로 다릅니다. 다시 불러와 주세요.")
    total = totals[0]
    if any(code in no_data_codes for code in codes) and (total or rows):
        raise AdminRegionLoadError("행정안전부 API의 처리 결과와 지역 건수가 일치하지 않습니다.")
    if rows is None:
        if total == 0:
            return [], 0
        raise AdminRegionLoadError("행정안전부 API 응답에 지역 목록이 없습니다. 다시 불러와 주세요.")
    if not all(isinstance(row, dict) for row in rows):
        raise AdminRegionLoadError("행정안전부 API 지역 목록에 잘못된 항목이 포함돼 있습니다.")
    return rows, total


def _admin_normalize_record(row):
    code = _admin_code(_admin_field(
        row, "region_cd", "region_code", "locatjumin_cd", "locatjijuk_cd"
    ))
    name = " ".join(_admin_field(row, "locatadd_nm", "region_name", "locataddNm").split())
    if not code or code == "0000000000" or not name:
        raise AdminRegionLoadError(
            "행정안전부 API 지역 항목의 법정동 코드 또는 지역명이 누락되거나 잘못됐습니다. "
            "불완전한 목록 대신 전체 목록을 다시 불러와 주세요."
        )
    result = {
        "region_code": code,
        "region_name": name,
        "sido_code": _admin_code(_admin_field(row, "sido_cd", "sido_code"), 2) or code[:2],
        "sigungu_code": _admin_code(_admin_field(row, "sgg_cd", "sigungu_code"), 3) or code[2:5],
        "eup_code": _admin_code(_admin_field(row, "umd_cd", "eup_code"), 3) or code[5:8],
        "li_code": _admin_code(_admin_field(row, "ri_cd", "li_code"), 2) or code[8:],
        "order": _admin_field(row, "locat_order", "order"),
        "remark": _admin_field(row, "locat_rm", "remark"),
        "high_code": _admin_field(row, "locathigh_cd", "high_code"),
        "low_name": _admin_field(row, "locallow_nm", "low_name"),
        "created": _admin_field(row, "adpt_de", "created"),
    }
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def load_admin_regions(api_key):
    """반환된 totalCount까지 모든 페이지를 검증한 경우에만 전국 목록을 캐시합니다."""
    if not _admin_text(api_key):
        return pd.DataFrame(columns=_ADMIN_COLUMNS)
    all_rows, seen_codes = [], set()
    expected_total = None
    for page in range(1, 1001):
        params = {
            "ServiceKey": str(api_key).strip(),
            "pageNo": page,
            "numOfRows": 1000,
            "type": "json",
        }
        try:
            response = requests.get(ADMIN_URL, params=params, timeout=(5, 20))
        except requests.Timeout:
            raise AdminRegionLoadError(
                f"전국 지역 목록 {page}페이지를 불러오다가 시간이 초과됐습니다. "
                "일부 지역만 표시하지 않도록 중단했습니다. 다시 불러와 주세요."
            ) from None
        except requests.RequestException:
            raise AdminRegionLoadError(
                f"전국 지역 목록 {page}페이지에 연결하지 못했습니다. 인터넷 연결을 확인하고 다시 불러와 주세요."
            ) from None
        if response.status_code in (401, 403):
            raise AdminRegionLoadError(
                "행정안전부 API 인증이 거절됐습니다. 활용신청 승인 여부와 Decoding 인증키를 확인해 주세요."
            )
        if response.status_code != 200:
            raise AdminRegionLoadError(
                f"전국 지역 목록 {page}페이지를 서버에서 받지 못했습니다. 잠시 후 다시 불러와 주세요."
            )
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise AdminRegionLoadError(
                "행정안전부 API가 JSON 대신 다른 형식의 응답을 보냈습니다. "
                "활용신청 승인 여부와 Decoding 인증키를 확인해 주세요."
            ) from None
        rows, total = _admin_decode_payload(payload)
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise AdminRegionLoadError(
                "불러오는 동안 전국 지역 목록의 전체 건수가 변경됐습니다. 최신 목록을 다시 불러와 주세요."
            )
        if not rows and len(all_rows) != expected_total:
            raise AdminRegionLoadError(
                f"전국 지역 목록 {page}페이지가 비어 있어 전체 목록을 완성하지 못했습니다. 다시 불러와 주세요."
            )
        for row in rows:
            normalized = _admin_normalize_record(row)
            if normalized["region_code"] in seen_codes:
                raise AdminRegionLoadError(
                    "행정안전부 API 페이지에 동일한 법정동 코드가 중복돼 전체 목록을 확인할 수 없습니다. "
                    "잠시 후 다시 불러와 주세요."
                )
            seen_codes.add(normalized["region_code"])
            all_rows.append(normalized)
        if len(all_rows) > expected_total:
            raise AdminRegionLoadError("행정안전부 API의 전체 건수와 실제 받은 건수가 다릅니다. 다시 불러와 주세요.")
        if len(all_rows) == expected_total:
            result = pd.DataFrame(all_rows, columns=_ADMIN_COLUMNS)
            result.attrs.update({"complete": True, "total_count": expected_total, "pages_loaded": page})
            return result
    raise AdminRegionLoadError(
        "전국 지역 목록의 페이지 수가 예상 범위를 넘어 불러오기를 중단했습니다. 전체 목록을 다시 불러와 주세요."
    )


def split_region_name(name):
    """법정동 주소에서 시+구를 보존하고, 리는 읍·면 수준으로 묶습니다."""
    parts = _admin_text(name).split()
    if not parts:
        return "", "", ""
    sido = _ADMIN_SIDO_ALIASES.get(parts[0], parts[0])
    if sido not in SIDO_COORDS:
        return "", "", ""
    remainder = parts[1:]
    if sido == "세종특별자치시":
        # 세종에는 시군구 중간 계층을 만들지 않습니다.
        return sido, "", remainder[0] if remainder else ""
    sigungu_parts = []
    while remainder and remainder[0].endswith(("시", "군", "구")):
        sigungu_parts.append(remainder.pop(0))
    if not sigungu_parts:
        return sido, "", ""
    # '세종로', '종로1가'처럼 이름이 동으로 끝나지 않는 법정동도 유지합니다.
    eupmyeondong = remainder[0] if remainder else ""
    return sido, " ".join(sigungu_parts), eupmyeondong


def build_region_dataframe(admin_df):
    """실제 선택 가능한 읍·면·동만 반환하고, 리/상위 집계 행 중복을 제거합니다."""
    if not isinstance(admin_df, pd.DataFrame) or admin_df.empty:
        return pd.DataFrame(columns=_REGION_COLUMNS)
    selected = {}
    for raw in admin_df.to_dict("records"):
        name = _admin_field(raw, "region_name", "locatadd_nm", "locataddNm")
        sido, sigungu, eup = split_region_name(name)
        if not sido or not eup or (not sigungu and sido != "세종특별자치시"):
            continue
        # 명확하게 폐지로 표시된 행만 제외합니다. 일반 비고는 추측해 해석하지 않습니다.
        if _admin_field(raw, "remark", "locat_rm") in {"폐지", "폐지됨", "말소"}:
            continue
        raw_code = _admin_code(_admin_field(
            raw, "region_cd", "region_code", "locatjumin_cd", "locatjijuk_cd"
        ))
        if not raw_code:
            continue
        # 법정리의 끝 2자리를 제거해 해당 읍/면/동의 법정동 코드로 올립니다.
        parent_code = raw_code[:8] + "00"
        identity = (sido, sigungu, eup)
        candidate = {
            "sido": sido, "sigungu": sigungu, "eupmyeondong": eup,
            "region_code": parent_code,
        }
        prior = selected.get(identity)
        if prior is None or raw_code.endswith("00"):
            selected[identity] = candidate
    if not selected:
        return pd.DataFrame(columns=_REGION_COLUMNS)
    return pd.DataFrame(selected.values(), columns=_REGION_COLUMNS).sort_values(
        ["sido", "sigungu", "eupmyeondong"]
    ).reset_index(drop=True)


def choose_random_region(regions, current=None):
    """시도 → 시군구 → 읍면동을 순서대로 추첨해 연결된 실제 지역 한 행을 반환합니다."""
    if isinstance(regions, pd.DataFrame):
        if not set(_REGION_COLUMNS).issubset(regions.columns):
            return None
        candidates = regions[_REGION_COLUMNS].copy()
    elif isinstance(regions, (list, tuple)) and regions:
        if not all(isinstance(row, dict) for row in regions):
            return None
        candidates = pd.DataFrame(regions)
        if not set(_REGION_COLUMNS).issubset(candidates.columns):
            return None
        candidates = candidates[_REGION_COLUMNS].copy()
    else:
        return None
    for column in _REGION_COLUMNS:
        candidates[column] = candidates[column].map(_admin_text)
    candidates = candidates[
        candidates["sido"].ne("")
        & candidates["eupmyeondong"].ne("")
        & candidates["region_code"].ne("")
        & (candidates["sigungu"].ne("") | candidates["sido"].eq("세종특별자치시"))
    ].drop_duplicates(["sido", "sigungu", "eupmyeondong"])
    if candidates.empty:
        return None
    identity_columns = ["sido", "sigungu", "eupmyeondong"]
    if isinstance(current, (dict, pd.Series)):
        identity = tuple(_admin_text(current.get(column)) for column in identity_columns)
    elif isinstance(current, (list, tuple)) and len(current) >= 3:
        identity = tuple(_admin_text(value) for value in current[:3])
    else:
        identity = None
    if identity is not None:
        is_current = pd.Series(True, index=candidates.index)
        for column, value in zip(identity_columns, identity):
            is_current &= candidates[column].eq(value)
        alternatives = candidates.loc[~is_current]
        if not alternatives.empty:
            candidates = alternatives
    picker = random.SystemRandom()
    sido = picker.choice(sorted(candidates["sido"].unique().tolist()))
    candidates = candidates[candidates["sido"].eq(sido)]
    sigungu = picker.choice(sorted(candidates["sigungu"].unique().tolist()))
    candidates = candidates[candidates["sigungu"].eq(sigungu)]
    result = picker.choice(candidates.to_dict("records"))
    return {column: str(result[column]) for column in _REGION_COLUMNS}


# ============================================================
# 8. 카카오 API 공통 오류 표시
# ============================================================

def kakao_error_message(response):

    try:
        data = response.json()

        error_type = data.get(
            "errorType",
            "",
        )

        message = data.get(
            "message",
            "",
        )

        if error_type and message:
            return (
                f"{error_type}: {message}"
            )

        if message:
            return str(message)

    except Exception:
        pass

    return response.text[:500]


# ============================================================
# 9. 카카오 주소 검색
# ============================================================

@st.cache_data(ttl=86400, show_spinner=False)
def kakao_address_search(
    address,
    api_key,
):

    if not api_key or not address:
        return None

    url = (
        "https://dapi.kakao.com/v2/local/"
        "search/address.json"
    )

    headers = {
        "Authorization": f"KakaoAK {api_key}"
    }

    params = {
        "query": address,
        "size": 1,
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=10,
        )

        if response.status_code != 200:
            return None

        data = response.json()

        documents = data.get(
            "documents",
            [],
        )

        if not documents:
            return None

        item = documents[0]

        return {
            "lat": float(item["y"]),
            "lon": float(item["x"]),
        }

    except Exception:
        return None


# ============================================================
# 10. 선택 지역 좌표
# ============================================================

def resolve_region_location(sido, sigungu, eup, api_key):
    """선택한 읍면동 좌표인지, 시도 표시용 대체 좌표인지 구분합니다."""
    full_address = " ".join(str(part).strip() for part in (sido, sigungu, eup) if part)
    if api_key and full_address:
        # 세종은 중간 시군구가 없어도 '세종특별자치시 보람동'을 그대로 검색합니다.
        result = kakao_address_search(full_address, api_key)
        if result:
            try:
                lat, lon = float(result["lat"]), float(result["lon"])
                if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    return {"lat": lat, "lon": lon, "coordinates_verified": True}
            except (KeyError, TypeError, ValueError):
                pass
    lat, lon = SIDO_COORDS.get(sido, (36.5, 127.8))
    return {"lat": lat, "lon": lon, "coordinates_verified": False}


def get_region_coordinate(sido, sigungu, eup, api_key):
    result = resolve_region_location(sido, sigungu, eup, api_key)
    return result["lat"], result["lon"]


# ============================================================
# 11. 카카오 키워드 검색
# 핵심 수정
# 기존:
# "가평군 맛집"
# 변경:
# 좌표 = 가평군
# 검색어 = "맛집"
# 카테고리 = FD6
# 즉, 선택 지역 좌표 주변을 검색한다.
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def kakao_keyword_search(
    query,
    lat,
    lon,
    api_key,
    category_code=None,
):

    if not api_key or not query:
        return [], None

    url = (
        "https://dapi.kakao.com/v2/local/"
        "search/keyword.json"
    )

    headers = {
        "Authorization": f"KakaoAK {api_key}"
    }

    params = {
        "query": query,
        "size": 15,
        "sort": "accuracy",
    }

    # 좌표가 있으면 해당 지역 중심으로 검색
    if lat is not None and lon is not None:

        params["x"] = float(lon)
        params["y"] = float(lat)

        # 반경 20km
        params["radius"] = 20000

    # 카테고리 코드
    if category_code:
        params["category_group_code"] = (
            category_code
        )

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=10,
        )

        if response.status_code != 200:

            return [], {
                "status": response.status_code,
                "message": kakao_error_message(
                    response
                ),
            }

        data = response.json()

        documents = data.get(
            "documents",
            [],
        )

        return documents, None

    except Exception as e:

        return [], {
            "status": -1,
            "message": str(e),
        }


# ============================================================
# 12. 카카오 결과 DataFrame
# ============================================================

def kakao_results_dataframe(results):

    if not results:
        return pd.DataFrame()

    rows = []

    for item in results:

        rows.append(
            {
                "name": item.get(
                    "place_name",
                    "이름 없음",
                ),
                "category": item.get(
                    "category_name",
                    "장소",
                ),
                "address": (
                    item.get(
                        "road_address_name"
                    )
                    or item.get(
                        "address_name"
                    )
                    or ""
                ),
                "phone": item.get(
                    "phone",
                    "",
                ),
                "distance": item.get(
                    "distance",
                    "",
                ),
                "lat": item.get(
                    "y",
                    "",
                ),
                "lon": item.get(
                    "x",
                    "",
                ),
                "url": item.get(
                    "place_url",
                    "",
                ),
            }
        )

    df = pd.DataFrame(rows)

    if not df.empty:

        df = df.drop_duplicates(
            subset=["name"]
        )

    return df


# ============================================================
# 13. 지역 장소 데이터 수집
# 핵심 수정:
# 각 카테고리를 지역명으로 검색하지 않는다.
# 예:
# 가평군 + 맛집
# X
# 가평군 좌표 + 맛집(FD6)
# O
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def collect_local_data(sido, sigungu, eup, lat, lon, api_key):
    data = {}
    location = " ".join(str(part) for part in (sido, sigungu, eup) if part)
    for category in ("맛집", "카페", "관광지", "숙박", "학교", "공원", "문화", "체험"):
        results, error = [], None
        code = CATEGORY_CODES.get(category)
        # 정확한 좌표가 있을 때만 반경 검색을 합니다.
        if code and lat is not None and lon is not None:
            results, error = kakao_keyword_search(category, lat, lon, api_key, code)
        if not results:
            results, fallback_error = kakao_keyword_search(
                f"{location} {category}", lat, lon, api_key, None
            )
            error = fallback_error or error
        frame = kakao_results_dataframe(results)
        if error and not results:
            frame.attrs["api_error"] = error
        data[category] = frame
    return data


# ============================================================
# 14. 지역 분석
# ============================================================

def calculate_region_analysis(
    local_data
):

    counts = {}

    for category, df in local_data.items():

        if df is None or df.empty:
            counts[category] = 0
        else:
            counts[category] = len(df)

    food = counts.get("맛집", 0)
    cafe = counts.get("카페", 0)
    tourism = counts.get("관광지", 0)
    lodging = counts.get("숙박", 0)
    school = counts.get("학교", 0)
    park = counts.get("공원", 0)
    culture = counts.get("문화", 0)
    experience = counts.get("체험", 0)

    def score(value):

        if value <= 0:
            return 0

        return min(
            100,
            round(
                math.log1p(value)
                / math.log1p(15)
                * 100
            ),
        )

    food_score = score(
        food + cafe
    )

    nature_score = score(
        tourism + park
    )

    culture_score = score(
        culture + experience
    )

    stay_score = score(
        lodging
    )

    overall = round(
        (
            food_score
            + nature_score
            + culture_score
            + stay_score
        ) / 4
    )

    return {
        "맛집": food,
        "카페": cafe,
        "관광지": tourism,
        "숙박": lodging,
        "학교": school,
        "공원": park,
        "문화": culture,
        "체험": experience,
        "food_score": food_score,
        "nature_score": nature_score,
        "culture_score": culture_score,
        "stay_score": stay_score,
        "overall_score": overall,
    }


# ============================================================
# 15. 지역 이름
# ============================================================

def location_text(region):

    parts = [
        region.get("sido", ""),
        region.get("sigungu", ""),
        region.get("eup", ""),
    ]

    return " ".join(
        [
            str(x)
            for x in parts
            if x
        ]
    )


# ============================================================
# 16. 지도
# ============================================================

def make_map(
    region,
    places=None,
):

    village_map = folium.Map(
        location=[
            region["lat"],
            region["lon"],
        ],
        zoom_start=12,
        control_scale=True,
        tiles="OpenStreetMap",
    )

    # 선택 지역
    folium.Marker(
        [
            region["lat"],
            region["lon"],
        ],
        tooltip="선택 지역",
        icon=folium.Icon(
            color="green",
            icon="home",
        ),
        popup=folium.Popup(
            f"""
            <b>
            {html.escape(
                    str(region["eup"])
            )}
            </b>
            <br>
            {html.escape(
                    str(region["sigungu"])
            )}
            <br>
            {html.escape(
                    str(region["sido"])
            )}
            """,
            max_width=300,
        ),
    ).add_to(village_map)

    # 장소 마커
    if places is not None and not places.empty:

        for _, row in places.iterrows():

            try:

                p_lat = float(
                    row["lat"]
                )

                p_lon = float(
                    row["lon"]
                )

            except Exception:
                continue

            category = str(
                row.get(
                    "main_category",
                    "장소",
                )
            )

            color = {
                "맛집": "red",
                "카페": "blue",
                "관광지": "green",
                "숙박": "orange",
                "학교": "purple",
                "공원": "green",
                "문화": "darkred",
                "체험": "cadetblue",
                "숨은 명소": "darkpurple",
            }.get(
                category,
                "gray",
            )

            folium.CircleMarker(
                [
                    p_lat,
                    p_lon,
                ],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.8,
                tooltip=str(
                    row["name"]
                ),
                popup=folium.Popup(
                    f"""
                    <b>
                    {html.escape(
                            str(row["name"])
                    )}
                    </b>
                    <br>
                    {html.escape(
                            str(category)
                    )}
                    <br>
                    {html.escape(
                            str(row["address"])
                    )}
                    """,
                    max_width=300,
                ),
            ).add_to(village_map)

    return village_map


# ============================================================
# 17. 데이터 합치기
# ============================================================

def combine_local_data(
    local_data
):

    frames = []

    for category, df in local_data.items():

        if df is None or df.empty:
            continue

        temp = df.copy()

        temp["main_category"] = category

        frames.append(temp)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(
        frames,
        ignore_index=True,
    )

    result = result.drop_duplicates(
        subset=["name"]
    )

    return result


# ============================================================
# 18. 지역 브리핑
# ============================================================

def render_home(
    region,
    analysis,
    local_data,
    interests,
    days,
):

    st.markdown(
        f"""
        <div class="hero">

        <div class="eyebrow">
        REAL LOCAL DATA PLATFORM
        </div>

        <h1>
        {html.escape(
                str(
                    region["eup"]
                    or region["sigungu"]
                    or region["sido"]
                )
        )}
        지역 분석
        </h1>

        <p>
        {html.escape(
                location_text(region)
        )}
        </p>

        <p>
        행정안전부 행정구역 데이터와
        카카오 Local API 실제 장소 데이터를
        기반으로 분석합니다.
        </p>

        </div>
        """,
        unsafe_allow_html=True,
    )

    metrics = st.columns(4)

    metrics[0].metric(
        "종합 지역 콘텐츠",
        f"{analysis['overall_score']}/100",
    )

    metrics[1].metric(
        "맛집",
        f"{analysis['맛집']}곳",
    )

    metrics[2].metric(
        "관광지",
        f"{analysis['관광지']}곳",
    )

    metrics[3].metric(
        "숙박",
        f"{analysis['숙박']}곳",
    )

    st.divider()

    left, right = st.columns(
        [1.4, 0.6]
    )

    with left:

        st.subheader(
            "실제 장소 데이터 지도"
        )

        all_places = combine_local_data(
            local_data
        )

        if all_places.empty:

            st.warning(
                "현재 지역에서 카카오 장소 데이터를 찾지 못했습니다."
            )

        else:

            st_folium(
                make_map(
                    region,
                    all_places,
                ),
                height=500,
                use_container_width=True,
                returned_objects=[],
            )

    with right:

        st.subheader(
            "지역 데이터 분석"
        )

        score_items = {
            "미식": analysis[
                "food_score"
            ],
            "자연·관광": analysis[
                "nature_score"
            ],
            "문화·체험": analysis[
                "culture_score"
            ],
            "숙박": analysis[
                "stay_score"
            ],
        }

        best = max(
            score_items,
            key=score_items.get,
        )

        st.success(
            f"가장 강한 분야: {best}"
        )

        for name, value in score_items.items():

            st.write(
                f"{name} {value}/100"
            )

            st.progress(
                value / 100
            )

        st.info(
            "관심사: "
            + (
                ", ".join(interests)
                if interests
                else "전체"
            )
        )

        st.info(
            f"추천 체류 기간: {days}일"
        )

    st.divider()

    st.subheader(
        "지역 콘텐츠 현황"
    )

    chart_data = pd.DataFrame(
        {
            "분야": [
                "맛집",
                "카페",
                "관광지",
                "숙박",
                "학교",
                "공원",
                "문화",
                "체험",
            ],
            "검색된 장소": [
                analysis["맛집"],
                analysis["카페"],
                analysis["관광지"],
                analysis["숙박"],
                analysis["학교"],
                analysis["공원"],
                analysis["문화"],
                analysis["체험"],
            ],
        }
    )

    fig = px.bar(
        chart_data,
        x="분야",
        y="검색된 장소",
        text="검색된 장소",
    )

    fig.update_layout(
        height=400,
        yaxis_title="검색된 장소 수",
        xaxis_title="",
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
    )


# ============================================================
# 19. 콘텐츠 탐색
# ============================================================

def render_explore(
    region,
    api_key,
):

    st.title(
        "지역 콘텐츠 탐색"
    )

    st.caption(
        "카카오 Local API 실제 장소 검색"
    )

    category = st.radio(
        "콘텐츠",
        [
            "맛집",
            "카페",
            "관광지",
            "숙박",
            "학교",
            "공원",
            "문화",
            "체험",
        ],
        horizontal=True,
    )

    location = location_text(
        region
    )

    # --------------------------------------------------------
    # 기본 검색어
    #
    # 중요:
    # 기본값을 "가평군 맛집"이 아니라 "맛집"으로 설정
    # 지역은 좌표로 제한한다.
    # --------------------------------------------------------

    query = st.text_input(
        "검색어",
        value=category,
        placeholder="예: 맛집, 카페, 막국수, 펜션",
    )

    st.caption(
        f"현재 검색 지역: {location}"
    )

    st.caption(
        "지역 중심 좌표에서 반경 20km 이내를 검색합니다."
    )

    if st.button(
        "🔎 검색",
        type="primary",
        use_container_width=True,
    ):

        if not api_key:

            st.error(
                "카카오 REST API 키를 입력하세요."
            )

            return

        code = CATEGORY_CODES.get(
            category
        )

        results = []
        api_error = None

        # ----------------------------------------------------
        # 1차:
        # 좌표 + 카테고리
        # ----------------------------------------------------

        if code:

            results, api_error = (
                kakao_keyword_search(
                    query,
                    region["lat"],
                    region["lon"],
                    api_key,
                    code,
                )
            )

        # ----------------------------------------------------
        # 2차:
        # 좌표 + 일반 키워드
        # ----------------------------------------------------

        if not results:

            results, fallback_error = (
                kakao_keyword_search(
                    query,
                    region["lat"],
                    region["lon"],
                    api_key,
                    None,
                )
            )

            if api_error is None:
                api_error = fallback_error

        # ----------------------------------------------------
        # 3차:
        # 검색어가 지역명 포함이면
        # 지역명을 제거하고 다시 검색
        # ----------------------------------------------------

        if not results:

            simple_query = category

            results, fallback_error = (
                kakao_keyword_search(
                    simple_query,
                    region["lat"],
                    region["lon"],
                    api_key,
                    code,
                )
            )

            if api_error is None:
                api_error = fallback_error

        # ----------------------------------------------------
        # API 오류
        # ----------------------------------------------------

        if api_error and not results:

            status = api_error.get(
                "status",
                "",
            )

            message = api_error.get(
                "message",
                "",
            )

            if status == 403:

                st.error(
                    "카카오 API 권한 오류입니다."
                )

                st.code(
                    f"HTTP 403\n{message}"
                )

                st.info(
                    "Kakao Developers에서 "
                    "OPEN_MAP_AND_LOCAL 서비스가 "
                    "활성화되어 있는지 확인하세요."
                )

                return

            elif status:

                st.error(
                    f"카카오 API 오류 "
                    f"(HTTP {status})"
                )

                st.code(
                    message
                )

                return

        # ----------------------------------------------------
        # 결과 없음
        # ----------------------------------------------------

        df = kakao_results_dataframe(
            results
        )

        if df.empty:

            st.warning(
                f"'{query}' 검색 결과가 없습니다."
            )

            st.info(
                "검색어를 짧게 입력해보세요. "
                "예: 맛집 / 카페 / 막국수"
            )

            return

        # ----------------------------------------------------
        # 결과 있음
        # ----------------------------------------------------

        df["main_category"] = category

        st.success(
            f"{len(df)}개의 장소를 찾았습니다."
        )

        # ----------------------------------------------------
        # 지도
        # ----------------------------------------------------

        st.subheader(
            "📍 검색 결과 지도"
        )

        st_folium(
            make_map(
                region,
                df,
            ),
            height=500,
            use_container_width=True,
            returned_objects=[],
        )

        st.divider()

        # ----------------------------------------------------
        # 결과 목록
        # ----------------------------------------------------

        st.subheader(
            "검색 결과"
        )

        for index, row in df.iterrows():

            st.markdown(
                f"### {index + 1}. {row['name']}"
            )

            if row["category"]:

                st.caption(
                    f"분류: {row['category']}"
                )

            if row["address"]:

                st.caption(
                    f"주소: {row['address']}"
                )

            if row["phone"]:

                st.caption(
                    f"전화: {row['phone']}"
                )

            if row["distance"]:

                st.caption(
                    f"거리: {row['distance']}m"
                )

            if row["url"]:

                st.link_button(
                    "카카오맵에서 보기",
                    row["url"],
                )

            st.divider()


# ============================================================
# 20. 대시보드
# ============================================================

def render_dashboard(
    region,
    analysis,
):

    st.title(
        "지역 활성화 데이터 대시보드"
    )

    st.caption(
        location_text(region)
    )

    chart_data = pd.DataFrame(
        {
            "분야": [
                "맛집",
                "카페",
                "관광지",
                "숙박",
                "학교",
                "공원",
                "문화",
                "체험",
            ],
            "장소 수": [
                analysis["맛집"],
                analysis["카페"],
                analysis["관광지"],
                analysis["숙박"],
                analysis["학교"],
                analysis["공원"],
                analysis["문화"],
                analysis["체험"],
            ],
        }
    )

    left, right = st.columns(2)

    with left:

        st.subheader(
            "지역 콘텐츠 규모"
        )

        fig = px.bar(
            chart_data,
            x="분야",
            y="장소 수",
            text="장소 수",
        )

        fig.update_layout(
            height=420
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
        )

    with right:

        st.subheader(
            "지역 자산 레이더"
        )

        labels = [
            "미식",
            "자연·관광",
            "문화·체험",
            "숙박",
        ]

        values = [
            analysis["food_score"],
            analysis["nature_score"],
            analysis["culture_score"],
            analysis["stay_score"],
        ]

        radar = go.Figure(
            go.Scatterpolar(
                r=values + [values[0]],
                theta=labels + [labels[0]],
                fill="toself",
            )
        )

        radar.update_layout(
            height=420,
            polar={
                "radialaxis": {
                    "visible": True,
                    "range": [0, 100],
                }
            },
        )

        st.plotly_chart(
            radar,
            use_container_width=True,
        )

    st.divider()

    st.subheader(
        "지역 소비 임팩트 시뮬레이터"
    )

    visitors = st.slider(
        "추가 방문객",
        500,
        20000,
        3000,
        500,
    )

    spend = st.slider(
        "1인 지역 소비",
        20000,
        200000,
        65000,
        5000,
    )

    stay = st.slider(
        "체류 전환율",
        5,
        70,
        28,
    )

    local_rate = st.slider(
        "지역 내 소비율",
        30,
        95,
        72,
    )

    total = visitors * spend

    local = int(
        total
        * local_rate
        / 100
    )

    overnight = int(
        visitors
        * stay
        / 100
    )

    a, b, c = st.columns(3)

    a.metric(
        "예상 지역 소비",
        f"{local:,}원",
    )

    b.metric(
        "추가 체류 방문객",
        f"{overnight:,}명",
    )

    c.metric(
        "총 소비 규모",
        f"{total:,}원",
    )


# ============================================================
# 21. AI 추천
# ============================================================

def render_ai(
    region,
    analysis,
    local_data,
    interests,
    days,
):

    st.title(
        "AI 지역 여행 추천"
    )

    st.caption(
        "카카오 Local API에서 실제 검색된 장소를 이용한 추천"
    )

    interest_map = {
        "자연": [
            "관광지",
            "공원",
        ],
        "미식": [
            "맛집",
            "카페",
        ],
        "역사": [
            "문화",
            "관광지",
        ],
        "휴식": [
            "카페",
            "숙박",
            "공원",
        ],
        "가족": [
            "관광지",
            "공원",
            "체험",
        ],
        "축제": [
            "문화",
            "체험",
        ],
    }

    candidate_categories = []

    for interest in interests:

        for category in interest_map.get(
            interest,
            [],
        ):

            if category not in candidate_categories:

                candidate_categories.append(
                    category
                )

    if not candidate_categories:

        candidate_categories = [
            "관광지",
            "맛집",
            "카페",
            "숙박",
        ]

    available_categories = [
        category
        for category in candidate_categories
        if (
            category in local_data
            and not local_data[
                category
            ].empty
        )
    ]

    if not available_categories:

        st.warning(
            "선택한 관심사에 해당하는 실제 장소 데이터가 없습니다."
        )

        return

    score_map = {
        "자연": analysis[
            "nature_score"
        ],
        "미식": analysis[
            "food_score"
        ],
        "역사": analysis[
            "culture_score"
        ],
        "휴식": analysis[
            "stay_score"
        ],
        "가족": (
            analysis[
                "nature_score"
            ]
            + analysis[
                "culture_score"
            ]
        ) // 2,
        "축제": analysis[
            "culture_score"
        ],
    }

    selected_scores = {
        interest: score_map.get(
            interest,
            0,
        )
        for interest in interests
    }

    if selected_scores:

        best = max(
            selected_scores,
            key=selected_scores.get,
        )

    else:

        best = "지역 탐색"

    st.success(
        f"이 지역에서는 {best} 분야를 중심으로 여행하는 것을 추천합니다."
    )

    st.divider()

    st.subheader(
        "실제 장소 기반 추천 코스"
    )

    day_plan = [
        ("오전", "관광지"),
        ("점심", "맛집"),
        ("오후", "카페"),
        ("저녁", "숙박"),
    ]

    for day in range(
        1,
        days + 1,
    ):

        st.markdown(
            f"## DAY {day}"
        )

        for time_label, category in day_plan:

            df = local_data.get(
                category,
                pd.DataFrame(),
            )

            if df.empty:
                continue

            index = (
                day - 1
            ) % len(df)

            row = df.iloc[index]

            icon = CATEGORY_ICONS.get(
                category,
                "📍",
            )

            st.markdown(
                f"### {icon} {time_label} · {row['name']}"
            )

            st.caption(
                f"{category} · {row['address']}"
            )

            if row["url"]:

                st.link_button(
                    "카카오맵에서 보기",
                    row["url"],
                )

            st.divider()


# ============================================================
# 22. 숨은 명소
# ============================================================

def render_hidden(
    region,
    api_key,
):

    st.title(
        "숨은 명소 탐색"
    )

    if not api_key:

        st.error(
            "카카오 REST API 키를 입력하세요."
        )

        return

    queries = [
        "산책",
        "공원",
        "전망대",
        "문화",
        "체험",
        "관광지",
    ]

    all_results = []

    location = location_text(
        region
    )

    api_error = None

    for query in queries:

        results, error = (
            kakao_keyword_search(
                query,
                region["lat"],
                region["lon"],
                api_key,
                None,
            )
        )

        if error and not results:
            api_error = error

        all_results.extend(
            results
        )

    if not all_results and api_error:

        st.error(
            f"카카오 API 오류 "
            f"(HTTP {api_error.get('status', '')})"
        )

        st.code(
            api_error.get(
                "message",
                "",
            )
        )

        return

    df = kakao_results_dataframe(
        all_results
    )

    if df.empty:

        st.warning(
            f"{location} 주변에서 숨은 명소 검색 결과가 없습니다."
        )

        return

    df["main_category"] = "숨은 명소"

    df = df.drop_duplicates(
        subset=["name"]
    ).head(30)

    left, right = st.columns(2)

    with left:

        for _, row in df.iterrows():

            st.markdown(
                f"### 📍 {row['name']}"
            )

            st.caption(
                f"{row['category']} · "
                f"{row['address']}"
            )

            if row["distance"]:

                st.caption(
                    f"거리: {row['distance']}m"
                )

            if row["url"]:

                st.link_button(
                    "카카오맵에서 보기",
                    row["url"],
                )

            st.divider()

    with right:

        st_folium(
            make_map(
                region,
                df,
            ),
            height=650,
            use_container_width=True,
            returned_objects=[],
        )




# ============================================================
# 예산 여행 코스: 실제 장소와 사용자가 조정한 비용 가정으로 계산
# ============================================================

_BUDGET_INTERESTS = {
    "자연": ["공원", "관광지"],
    "미식": [],
    "역사": ["문화", "관광지"],
    "휴식": ["공원", "관광지"],
    "가족": ["체험", "공원", "관광지"],
    "축제": ["문화", "체험"],
}
_BUDGET_SIGHTS = {"관광지", "공원", "문화", "체험"}


def _budget_haversine(a, b):
    """직선거리(km). 도로 경로나 실시간 길찾기 결과가 아니다."""
    lat1, lon1, lat2, lon2 = map(math.radians, [*a, *b])
    q = math.sin((lat2 - lat1) / 2) ** 2
    q += math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(max(0.0, q))))


def _budget_point(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None


def _budget_places(local_data, region):
    """학교·중복·좌표 없는 검색 결과를 제외한다. 이름을 만들어 넣지 않는다."""
    places, seen = [], set()
    center = _budget_point(region.get("lat"), region.get("lon"))
    allowed = _BUDGET_SIGHTS | {"맛집", "카페", "숙박"}
    for category, frame in (local_data or {}).items():
        if category not in allowed or frame is None or frame.empty:
            continue
        for row in frame.to_dict("records"):
            point = _budget_point(row.get("lat"), row.get("lon"))
            name = str(row.get("name", "")).strip()
            address = str(row.get("address", "") or "").strip()
            if not point or not name or name.lower() in {"nan", "none", "이름 없음"}:
                continue
            if center and region.get("coordinates_verified") and _budget_haversine(center, point) > 30:
                continue
            url = str(row.get("url", "") or "").strip()
            if not url.startswith(("https://place.map.kakao.com/", "http://place.map.kakao.com/")):
                url = ""
            identity = url or f"{name}|{address}"
            if identity in seen:
                continue
            seen.add(identity)
            places.append({"name": name, "address": address, "lat": point[0], "lon": point[1],
                           "url": url, "main_category": category, "identity": identity})
    return places


def _budget_island_route(origin, region):
    """확실한 도서 지명은 육로 거리 추정에서 제외한다. 모든 섬을 판별하지는 못한다."""
    destination = " ".join(str(region.get(k, "")) for k in ("sido", "sigungu", "eup"))
    if ("제주" in origin) != ("제주" in destination):
        return True
    island_names = ("울릉", "우도", "추자", "백령", "대청", "소청", "연평", "덕적", "자월", "흑산", "홍도", "가거", "청산면", "노화읍", "보길면", "소안면", "금일읍", "생일면", "욕지면", "사량면", "한산면", "남면 연도")
    return any(name in origin or name in destination for name in island_names)


def _budget_geocode_origin(origin, api_key):
    """주소 우선, 역명/장소명은 기존 카카오 키워드 검색으로 보완한다."""
    if not api_key or not origin.strip():
        return None
    result = kakao_address_search(origin.strip(), api_key)
    if result and _budget_point(result.get("lat"), result.get("lon")):
        return {"lat": result["lat"], "lon": result["lon"], "label": origin.strip()}
    documents, _ = kakao_keyword_search(origin.strip(), None, None, api_key)
    for document in documents[:1]:
        point = _budget_point(document.get("y"), document.get("x"))
        if point:
            address = document.get("road_address_name") or document.get("address_name") or ""
            return {"lat": point[0], "lon": point[1],
                    "label": f"{document.get('place_name', origin)} · {address}"}
    return None


def _budget_estimate_transport(settings, distance_km=None):
    """반환: ok, roundtrip_cost(일행 전체), one_way_minutes, local_cost, note."""
    s = settings
    people, days = int(s["people"]), int(s["days"])
    local_cost = int(s["local_daily"]) * days * (people if s["mode"] == "대중교통" else 1)
    if s["transport_input"] == "왕복 교통비 직접 입력":
        return {"ok": True, "roundtrip_cost": int(s["manual_cost"]),
                "one_way_minutes": int(s["manual_minutes"]), "local_cost": local_cost,
                "note": "직접 입력한 일행 전체 왕복비와 편도 소요시간. 현지 이동비는 별도입니다."}
    if distance_km is None or not math.isfinite(distance_km) or distance_km < 0:
        return {"ok": False, "error": "출발지와 목적지의 좌표가 필요합니다. 왕복 교통비 직접 입력을 선택할 수도 있어요."}
    route_km = distance_km * float(s["road_factor"])
    one_way_minutes = int(math.ceil(route_km / float(s["speed"]) * 60 + int(s["boarding_minutes"])))
    if s["mode"] == "자가용":
        fare = int(math.ceil(route_km * 2 * int(s["km_cost"]))) + int(s["extra_transport"])
        note = f"직선 {distance_km:.1f}km × {s['road_factor']:.2f} = 편도 추정 {route_km:.1f}km. 차량 1대 왕복 주행비 + 통행료 가정."
    else:
        one_way_fare = max(int(s["minimum_fare"]), int(math.ceil(route_km * int(s["km_cost"]))))
        fare = one_way_fare * 2 * people
        note = f"직선 {distance_km:.1f}km × {s['road_factor']:.2f} = 편도 추정 {route_km:.1f}km. 거리당 운임 가정으로 계산한 {people}명 왕복 예상비."
    return {"ok": True, "roundtrip_cost": fare, "one_way_minutes": one_way_minutes,
            "local_cost": local_cost, "note": note, "distance_km": distance_km, "route_km": route_km}


def _budget_costs(settings, transport, visits, cafes):
    s = settings
    people, days, nights = int(s["people"]), int(s["days"]), int(s["days"]) - 1
    rooms = math.ceil(people / int(s["room_people"])) if nights else 0
    rows = [
        {"항목": "출발지 ↔ 여행지 왕복 교통", "금액(원)": int(transport["roundtrip_cost"]), "기준": "일행 전체 / 왕복"},
        {"항목": "여행지 내 이동", "금액(원)": int(transport["local_cost"]), "기준": f"{days}일 현지 이동 예상비"},
        {"항목": "식비", "금액(원)": int(s["meal_cost"]) * int(s["meals"]) * people * days,
         "기준": f"1인 1끼 {s['meal_cost']:,}원 × {s['meals']}끼 × {people}명 × {days}일"},
        {"항목": "숙박비", "금액(원)": int(s["lodging"]) * rooms * nights,
         "기준": "당일치기: 숙박비 없음" if not nights else f"1실 1박 {s['lodging']:,}원 × {rooms}실 × {nights}박"},
        {"항목": "입장·체험 예상비", "금액(원)": int(s["activity"]) * people * visits,
         "기준": f"1인 1곳 {s['activity']:,}원 × {people}명 × {visits}곳 (실제 가격 확인 필요)"},
        {"항목": "카페 예상비", "금액(원)": int(s["cafe"]) * people * cafes,
         "기준": f"1인 1회 {s['cafe']:,}원 × {people}명 × {cafes}회"},
    ]
    subtotal = sum(row["금액(원)"] for row in rows)
    reserve = int(math.ceil(subtotal * int(s["reserve_pct"]) / 100))
    rows.append({"항목": "예비비", "금액(원)": reserve, "기준": f"위 예상 지출 합계의 {s['reserve_pct']}%"})
    return rows, subtotal + reserve


def _budget_clock(minutes):
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _budget_schedule(settings, places, transport, visit_counts, cafe_counts):
    """일별 가용시간 안에서 최근접 장소를 선택한다. 실패 시 부분 코스를 반환하지 않는다."""
    s = settings
    used, itinerary, chosen = set(), [], []
    priority = []
    for interest in s.get("interests", []):
        for category in _BUDGET_INTERESTS.get(interest, []):
            if category not in priority:
                priority.append(category)
    priority += [category for category in ("관광지", "공원", "문화", "체험") if category not in priority]
    center = tuple(s["center"])
    local_speed = 28 if s["mode"] == "자가용" else 16
    day_return = int(transport["one_way_minutes"])
    lodging_choices = [p for p in places if p["main_category"] == "숙박"]
    lodging = min(lodging_choices, key=lambda p: _budget_haversine(center, (p["lat"], p["lon"]))) if lodging_choices else None
    overnight_point = (lodging["lat"], lodging["lon"]) if lodging else center

    def append(day, start, end, kind, name, note="", place=None):
        itinerary.append({"일차": day, "시작": _budget_clock(start), "종료": _budget_clock(end),
                          "구분": kind, "일정/장소": name, "주소": place["address"] if place else "",
                          "카카오맵": place["url"] if place else "", "안내": note})

    for day in range(1, s["days"] + 1):
        now = int(s["start_minutes"])
        cutoff = int(s["end_minutes"]) - (day_return if day == s["days"] else 0)
        position = center if day == 1 else overnight_point

        def wait_until(target):
            nonlocal now
            if now < target:
                append(day, now, target, "휴식", "자유 시간 / 휴식", "개별 지출은 예비비에서 확인")
                now = target

        def move_to(point, name):
            nonlocal now, position
            direct = _budget_haversine(position, point)
            if direct < 0.05:
                position = point
                return
            if s["mode"] == "대중교통" and direct <= 0.8:
                duration = max(5, math.ceil(direct * 1.3 / 4 * 60))
                detail = "가까운 구간 도보 추정"
            else:
                duration = max(10, math.ceil(direct * 1.3 / local_speed * 60))
                if s["mode"] == "대중교통":
                    duration += 10
                detail = "직선거리 × 1.3 / 평균속도. 실제 노선·운행 여부 확인 필요"
            append(day, now, now + duration, "현지 이동", name, detail)
            now += duration
            position = point

        def choose(categories):
            candidates = [p for p in places if p["main_category"] in categories and p["identity"] not in used]
            if not candidates:
                return None
            def score(place):
                distance = _budget_haversine(position, (place["lat"], place["lon"]))
                rank = priority.index(place["main_category"]) if place["main_category"] in priority else 0
                return distance + rank * 1.5
            return min(candidates, key=score)

        def stop(place, duration, kind, fallback, note=""):
            nonlocal now
            if place:
                move_to((place["lat"], place["lon"]), f"{place['name']} 이동")
                used.add(place["identity"])
                chosen.append(place)
            append(day, now, now + duration, kind, place["name"] if place else fallback, note, place)
            now += duration

        if s["meals"] == 3:
            stop(None, 40, "아침", "아침 식사 · 직접 선택", "식비에 포함")
        if day == 1:
            append(day, now, now + day_return, "가는 교통", f"{s['origin']} → 선택 여행지", "예상 소요시간 / 왕복 교통비에 포함")
            now += day_return
        if now > 14 * 60:
            return {"ok": False, "error": "목적지 도착이 오후 2시 이후라 첫날 점심과 방문 일정을 넣기 어렵습니다. 출발을 앞당기거나 가까운 지역을 선택해 주세요."}

        # 점심 전 시간이 충분하면 첫 명소를 방문하고, 늦게 도착하면 식사를 먼저 한다.
        before_lunch = bool(visit_counts[day - 1] and now + 100 <= 13 * 60)
        if before_lunch:
            sight = choose(_BUDGET_SIGHTS)
            if not sight:
                return {"ok": False, "error": "중복 없이 배치할 실제 방문 장소가 부족합니다."}
            stop(sight, 75, "방문", "", "영업일·운영시간·입장료는 방문 전 확인")
        wait_until(11 * 60 + 30)
        if now > 14 * 60:
            return {"ok": False, "error": "현지 이동시간 때문에 점심 일정이 너무 늦어집니다. 가까운 지역이나 다른 교통수단을 선택해 주세요."}
        stop(choose({"맛집"}), 50, "점심", "점심 식사 · 식당 직접 선택", "입력한 1인 1끼 예상비 사용 / 메뉴 가격 미확인")
        for _ in range(visit_counts[day - 1] - int(before_lunch)):
            sight = choose(_BUDGET_SIGHTS)
            if not sight:
                return {"ok": False, "error": "중복 없이 배치할 실제 방문 장소가 부족합니다."}
            stop(sight, 75, "방문", "", "영업일·운영시간·입장료는 방문 전 확인")
        if cafe_counts[day - 1]:
            cafe = choose({"카페"})
            if not cafe:
                return {"ok": False, "error": "중복 없는 카페 검색 결과가 부족합니다."}
            stop(cafe, 40, "카페", "", "입력한 음료 예상비 사용 / 메뉴 가격 미확인")
        append(day, now, now + 20, "휴식", "휴식 / 일정 여유", "추가 지출은 예비비에서 확인")
        now += 20
        if s["meals"] >= 2:
            wait_until(17 * 60)
            stop(choose({"맛집"}), 50, "저녁", "저녁 식사 · 식당 직접 선택", "입력한 1인 1끼 예상비 사용 / 메뉴 가격 미확인")
        if day == s["days"]:
            move_to(center, "여행지 출발 지점으로 이동")
            if now > cutoff:
                return {"ok": False, "error": "왕복 이동·식사·최소 방문시간을 더하면 귀가 마감시간을 넘습니다. 더 가까운 지역, 빠른 출발, 늦은 귀가 또는 숙박 여행을 선택해 주세요."}
            append(day, now, now + day_return, "오는 교통", f"선택 여행지 → {s['origin']}", "귀가 / 왕복 교통비에 포함")
            now += day_return
        else:
            move_to(overnight_point, "숙소 방향으로 이동")
            append(day, now, now + 30, "숙박", lodging["name"] if lodging else "숙소 · 직접 예약", "입력한 객실 예상비 기준 / 요금·예약 가능 여부 미확인", lodging)
            now += 30
            if lodging and lodging["identity"] not in {p["identity"] for p in chosen}:
                chosen.append(lodging)
        if now > int(s["end_minutes"]):
            return {"ok": False, "error": "하루 이동·식사·방문시간이 설정한 하루 마감시간을 넘습니다. 시작/마감시간이나 여행지를 조정해 주세요."}
    return {"ok": True, "itinerary": itinerary, "selected_places": chosen}


def _budget_make_plan(settings, places, transport):
    """순수 계산 진입점. over_budget/time_infeasible 상태에는 실행 가능한 코스를 내지 않는다."""
    s = settings
    base = {"ok": False, "status": "invalid", "itinerary": [], "selected_places": [], "warnings": [],
            "breakdown": [], "total": 0, "remaining": int(s.get("budget", 0)), "minimum_required": 0, "shortfall": 0}
    if not str(s.get("origin", "")).strip():
        return {**base, "error": "출발지 주소나 역명을 입력해 주세요. 왕복 교통비 직접 입력도 출발지 이름이 필요합니다."}
    if int(s["budget"]) <= 0:
        return {**base, "error": "일행 전체 여행 예산을 0원보다 크게 입력해 주세요."}
    if not 1 <= int(s["days"]) <= 3 or int(s["people"]) < 1:
        return {**base, "error": "여행 일수는 1~3일, 여행 인원은 1명 이상이어야 합니다."}
    if s["mode"] == "자가용" and int(s["people"]) > 5 and s["transport_input"] != "왕복 교통비 직접 입력":
        return {**base, "error": "6명 이상은 차량 수에 따른 전체 왕복비를 직접 입력해 주세요."}
    if int(s["end_minutes"]) <= int(s["start_minutes"]):
        return {**base, "error": "하루 마감시간은 시작시간보다 늦게 설정해 주세요."}
    if not transport.get("ok"):
        return {**base, "error": transport.get("error", "교통비를 계산할 수 없습니다.")}
    if int(transport["one_way_minutes"]) <= 0:
        return {**base, "error": "편도 이동시간은 1분 이상 입력해 주세요. 대기·환승 시간도 포함해 주세요."}
    days = int(s["days"])
    sights_available = sum(p["main_category"] in _BUDGET_SIGHTS for p in places)
    cafes_available = sum(p["main_category"] == "카페" for p in places)
    minimum_visits = min(days, sights_available) if sights_available else days
    minimum_rows, minimum_total = _budget_costs(s, transport, minimum_visits, 0)
    base.update({"breakdown": minimum_rows, "total": minimum_total, "remaining": s["budget"] - minimum_total,
                 "minimum_required": minimum_total, "shortfall": max(0, minimum_total - s["budget"])})
    if minimum_total > s["budget"]:
        return {**base, "status": "over_budget", "error": f"설정한 교통·식사·숙박·최소 방문·예비비 기준으로 {minimum_total - s['budget']:,}원이 부족합니다. 실제 비용을 확인해 가정을 바꾸거나 예산·여행지를 조정해 주세요."}
    min_daily_minutes = int(s["meals"]) * 50 + 75 + 20
    available_minutes = (int(s["end_minutes"]) - int(s["start_minutes"])) * days
    if int(transport["one_way_minutes"]) * 2 + min_daily_minutes * days > available_minutes:
        return {**base, "status": "time_infeasible", "error": "설정한 왕복 이동시간과 최소 식사·방문시간만으로 하루 가용시간을 초과합니다. 가까운 여행지를 고르거나 여행 일수를 늘려 주세요."}
    if not sights_available:
        return {**base, "ok": True, "status": "budget_only", "warnings": ["실제 관광·공원·문화·체험 장소가 없어 예산 계산만 제공합니다. 하루 1곳 방문비를 임시 배정했으며, 실제 여행 코스는 생성하지 않았습니다."]}
    # 최소 방문을 하루씩 배치하고 여유 예산으로 방문지와 카페를 늘린다.
    base_counts = [1 if d < min(days, sights_available) else 0 for d in range(days)]
    variants = []
    max_extra = min(days, max(0, sights_available - sum(base_counts)))
    wants_cafe = any(x in s.get("interests", []) for x in ("미식", "휴식"))
    max_cafes = min(days, cafes_available) if wants_cafe else 0
    for extras in range(max_extra, -1, -1):
        for cafe_total in range(max_cafes, -1, -1):
            visits = [count + int(d < extras) for d, count in enumerate(base_counts)]
            cafes = [int(d < cafe_total) for d in range(days)]
            rows, total = _budget_costs(s, transport, sum(visits), sum(cafes))
            if total <= s["budget"]:
                variants.append((sum(visits) + sum(cafes), visits, cafes, rows, total))
    # 미식·휴식 관심이면 같은 방문 수에서 카페가 포함된 조합을 먼저 고른다.
    variants.sort(key=lambda v: (v[0], sum(v[2]) if wants_cafe else 0), reverse=True)
    last_error = "이동시간과 방문시간이 맞는 코스를 만들지 못했습니다. 지역이나 시간 설정을 조정해 주세요."
    for _, visits, cafes, rows, total in variants:
        schedule = _budget_schedule(s, places, transport, visits, cafes)
        if not schedule["ok"]:
            last_error = schedule["error"]
            continue
        warnings = ["모든 금액은 입력한 가정에 따른 예상치입니다. 카카오 검색에는 실제 교통 운임·메뉴 가격·입장료·객실 요금·예약 가능 여부가 포함되지 않습니다.",
                    "표의 시간은 실제 노선표나 운영시간이 아닙니다. 방문 전 카카오맵에서 이동 경로와 장소 영업시간을 확인해 주세요."]
        if sights_available < days:
            warnings.append("검색된 방문 장소가 여행 일수보다 적어 일부 날은 식사·휴식 위주입니다. 장소를 반복해 채우지 않았습니다.")
        if s["activity"] == 0:
            warnings.append("입장·체험비를 0원으로 설정했습니다. 방문지가 무료라고 확인된 것은 아니므로 각 장소의 비용을 확인해 주세요.")
        if sum(visits) < days * 2 or sum(cafes) < max_cafes:
            warnings.append("예산과 하루 가용시간에 맞춰 방문지·카페 수를 조정했습니다.")
        return {**base, **schedule, "ok": True, "status": "ready", "breakdown": rows, "total": total,
                "remaining": s["budget"] - total, "shortfall": 0, "warnings": warnings,
                "visit_count": sum(visits), "cafe_count": sum(cafes)}
    return {**base, "status": "time_infeasible", "error": last_error}


def _budget_fingerprint(settings, region, places, api_key=""):
    payload = {"settings": settings, "region": {k: str(region.get(k, "")) for k in ("sido", "sigungu", "eup", "region_code", "coordinates_verified")},
               "places": places, "api_key_digest": hashlib.sha256(api_key.encode("utf-8")).hexdigest()}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _budget_csv(rows):
    # 외부 장소명이 스프레드시트 수식으로 해석되지 않도록 방어한다.
    def safe_cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value
    return pd.DataFrame([{key: safe_cell(value) for key, value in row.items()} for row in rows]).to_csv(index=False).encode("utf-8-sig")


def render_budget_trip(region, local_data, interests, days, api_key):
    st.title("예산에 맞는 여행 코스")
    st.caption("선택한 지역의 실제 장소를 연결하고 왕복 교통·현지 이동·식사·숙박·예비비를 함께 계산해요.")
    st.caption("자연·역사 등은 방문지 종류에, 미식·휴식은 카페 배치에 반영해요. 식당은 코스 주변의 실제 검색 결과에서 골라요.")
    duration = "당일치기" if days == 1 else f"{days - 1}박 {days}일"
    st.info(f"여행지: {location_text(region)} · {duration} · 여행 기간과 관심사는 왼쪽에서 변경할 수 있어요.")
    c1, c2 = st.columns([2, 1])
    with c1:
        budget = st.number_input("여행 예산 · 일행 전체 총액 (원)", min_value=0, value=150000, step=10000, key="trip_budget")
    with c2:
        people = st.number_input("여행 인원 (명)", min_value=1, max_value=20, value=1, step=1, key="trip_people")
    origin = st.text_input("출발지 주소 또는 역명", placeholder="예: 서울역, 경기도 수원시 영통구 ...", key="trip_origin")
    c1, c2 = st.columns(2)
    with c1:
        mode = st.selectbox("교통수단", ["대중교통", "자가용"], key="trip_mode")
    with c2:
        transport_input = st.selectbox("왕복 교통비 계산 방법", ["거리로 대략 계산", "왕복 교통비 직접 입력"], key="trip_transport_input")
    c1, c2 = st.columns(2)
    with c1:
        start_time = st.time_input("하루 시작 시각", value=trip_clock_time(8, 0), step=900, key="trip_start")
    with c2:
        end_time = st.time_input("귀가 / 숙소 도착 마감", value=trip_clock_time(21, 0), step=900, key="trip_end")
    route_type = st.selectbox("이동 경로 종류", ["육로 이동", "항공·배편 포함"], key="trip_route_type")
    air_ferry = route_type == "항공·배편 포함"
    manual_cost, manual_minutes = 0, 60
    if transport_input == "왕복 교통비 직접 입력":
        c1, c2 = st.columns(2)
        with c1:
            manual_cost = st.number_input("출발지 ↔ 여행지 왕복비 · 일행 전체 (원)", min_value=0, value=40000, step=1000, key="trip_manual_cost")
        with c2:
            manual_minutes = st.number_input("편도 이동시간 · 대기/환승 포함 (분)", min_value=1, max_value=1440, value=90, step=10, key="trip_manual_minutes")
        st.caption("왕복비는 모든 인원·차량의 합계예요. 항공·배편과 여러 차량의 요금도 합쳐 입력하고, 여행지 안에서 쓰는 이동비는 아래에 따로 입력해 주세요.")
    else:
        st.caption("육로 이동을 기준으로 직선거리에 도로 우회계수를 곱한 거친 예상입니다. 실제 길찾기·열차/버스 요금·배차 정보가 아닙니다. 섬·항공·배편은 왕복비와 편도시간을 직접 입력해 주세요.")

    with st.expander("식비·숙박비·교통비 가정 조정", expanded=False):
        st.caption("아래 기본값은 계산용 예시 금액입니다. 확인한 실제 가격이나 본인의 소비 계획에 맞춰 변경해 주세요.")
        c1, c2, c3 = st.columns(3)
        with c1:
            meal_cost = st.number_input("식비 · 1인 1끼 (원)", min_value=0, value=12000, step=1000, key="trip_meal_cost")
            meals = st.selectbox("식사 횟수 · 1인 하루", [1, 2, 3], index=1, key="trip_meals",
                                 help="1끼: 점심 / 2끼: 점심·저녁 / 3끼: 아침·점심·저녁")
        with c2:
            activity = st.number_input("입장·체험비 · 1인 1곳 (원)", min_value=0, value=10000, step=1000, key="trip_activity")
            cafe = st.number_input("카페 · 1인 1회 (원)", min_value=0, value=6000, step=1000, key="trip_cafe")
        with c3:
            local_daily = st.number_input("현지 이동비 · 1인 하루 (원)" if mode == "대중교통" else "현지 주행·주차비 · 차량 전체 하루 (원)",
                                          min_value=0, value=8000 if mode == "대중교통" else 15000, step=1000,
                                          key="trip_local_transit" if mode == "대중교통" else "trip_local_car")
            reserve_pct = st.number_input("예비비 · 예상 지출의 (%)", min_value=0, max_value=50, value=10, step=1, key="trip_reserve")
        lodging, room_people = 0, 2
        if days > 1:
            c1, c2 = st.columns(2)
            with c1:
                lodging = st.number_input("숙박비 · 객실 1개 1박 (원)", min_value=0, value=80000, step=10000, key="trip_lodging")
            with c2:
                room_people = st.number_input("객실 1개당 인원 (명)", min_value=1, max_value=20, value=2, step=1, key="trip_room_people")
        else:
            st.caption("당일치기는 숙박비를 0원으로 계산해요.")
        road_factor, km_cost, minimum_fare, extra_transport, speed, boarding_minutes = 1.3, 150, 5000, 10000, 60, 30
        if transport_input == "거리로 대략 계산":
            c1, c2, c3 = st.columns(3)
            with c1:
                road_factor = st.number_input("도로 우회계수 · 직선거리 배수", min_value=1.0, max_value=3.0, value=1.3, step=0.1, key="trip_road_factor")
                speed = st.number_input("장거리 평균 이동속도 가정 (km/h)", min_value=10, max_value=200, value=60, step=5, key="trip_speed")
            with c2:
                km_cost = st.number_input("1인 편도 1km당 운임 가정 (원)" if mode == "대중교통" else "차량 1km당 주행비 가정 (원)",
                                          min_value=0, value=150 if mode == "대중교통" else 200, step=10,
                                          key="trip_km_transit" if mode == "대중교통" else "trip_km_car")
                boarding_minutes = st.number_input("편도 추가 여유 · 대기/환승 (분)", min_value=0, max_value=240, value=30, step=5, key="trip_boarding")
            with c3:
                if mode == "대중교통":
                    minimum_fare = st.number_input("1인 편도 최소운임 가정 (원)", min_value=0, value=5000, step=1000, key="trip_minimum_fare")
                else:
                    extra_transport = st.number_input("왕복 통행료 · 차량 1대 합계 (원)", min_value=0, value=10000, step=1000, key="trip_toll")
            st.caption("자동 계산은 자가용 1대 기준입니다. 6명 이상 또는 여러 차량은 왕복비를 직접 입력해 주세요. 현지 이동시간도 거리 추정이므로 실제 교통편을 반드시 확인해 주세요.")

    places = _budget_places(local_data, region)
    center = _budget_point(region.get("lat"), region.get("lon")) or (36.5, 127.8)
    if not region.get("coordinates_verified") and places:
        first_place = next((p for p in places if p["main_category"] in _BUDGET_SIGHTS), places[0])
        center = (first_place["lat"], first_place["lon"])
    settings = {"budget": int(budget), "people": int(people), "origin": origin.strip(), "days": int(days),
                "mode": mode, "transport_input": transport_input, "manual_cost": int(manual_cost), "manual_minutes": int(manual_minutes),
                "start_minutes": start_time.hour * 60 + start_time.minute, "end_minutes": end_time.hour * 60 + end_time.minute,
                "air_ferry": air_ferry, "meal_cost": int(meal_cost), "meals": int(meals),
                "activity": int(activity), "cafe": int(cafe), "local_daily": int(local_daily), "reserve_pct": int(reserve_pct),
                "lodging": int(lodging), "room_people": int(room_people), "road_factor": float(road_factor), "km_cost": int(km_cost),
                "minimum_fare": int(minimum_fare), "extra_transport": int(extra_transport), "speed": int(speed),
                "boarding_minutes": int(boarding_minutes), "center": center, "interests": list(interests)}
    fingerprint = _budget_fingerprint(settings, region, places, api_key)
    if st.session_state.get("trip_result_fingerprint") not in (None, fingerprint):
        st.session_state.pop("trip_result", None)
        st.session_state.pop("trip_result_fingerprint", None)
        st.info("지역이나 조건이 바뀌어 이전 코스를 지웠어요. 새 조건으로 다시 만들어 주세요.")

    if st.button("예산에 맞춰 코스 만들기", type="primary", key="trip_generate", use_container_width=True):
        error, origin_result, transport = None, None, None
        if not origin.strip():
            error = "출발지 주소나 역명을 입력해 주세요."
        elif budget <= 0:
            error = "일행 전체 예산을 0원보다 크게 입력해 주세요."
        elif transport_input == "거리로 대략 계산":
            if air_ferry or _budget_island_route(origin, region):
                error = "항공·배편 또는 도서 지역은 육로 거리로 계산할 수 없어요. 왕복 교통비 직접 입력을 선택해 실제 예상 왕복비와 편도시간을 넣어 주세요."
            elif not api_key:
                error = "거리 계산에는 카카오 REST API 키가 필요해요. 키가 없으면 왕복 교통비 직접 입력을 이용할 수 있어요."
            elif not region.get("coordinates_verified") and not places:
                error = "선택 지역의 정확한 좌표를 확인하지 못했어요. 왕복 교통비와 편도시간을 직접 입력해 주세요."
            else:
                with st.spinner("출발지와 왕복 이동 거리를 확인하고 있어요..."):
                    origin_result = _budget_geocode_origin(origin, api_key)
                if not origin_result:
                    error = "출발지를 찾지 못했어요. 시·군·구를 포함한 주소나 정확한 역명을 입력하거나 왕복 교통비를 직접 입력해 주세요."
                else:
                    distance = _budget_haversine((origin_result["lat"], origin_result["lon"]), center)
                    transport = _budget_estimate_transport(settings, distance)
        else:
            transport = _budget_estimate_transport(settings)
        if error:
            st.session_state.pop("trip_result", None)
            st.error(error)
        else:
            result = _budget_make_plan(settings, places, transport)
            result["transport"] = transport
            result["origin_resolved"] = origin_result["label"] if origin_result else origin.strip()
            if not region.get("coordinates_verified") and places:
                result["warnings"].append("행정구역 좌표 대신 실제 검색 장소의 좌표를 도착 기준점으로 사용했습니다.")
            st.session_state["trip_result"] = result
            st.session_state["trip_result_fingerprint"] = fingerprint

    result = st.session_state.get("trip_result")
    if not result or st.session_state.get("trip_result_fingerprint") != fingerprint:
        return
    st.divider()
    if result.get("error"):
        st.error(result["error"])
    elif result["status"] == "ready":
        st.success("입력한 예상비 기준으로 예산과 하루 가용시간 안에 들어오는 코스를 만들었어요.")
    elif result["status"] == "budget_only":
        st.warning("예산 계산 완료 · 실제 장소가 없어 여행 코스는 아직 만들지 않았어요.")
    for warning in result.get("warnings", []):
        st.caption(warning)
    if not result["breakdown"]:
        return
    a, b, c = st.columns(3)
    a.metric("전체 예산", f"{budget:,}원")
    b.metric("예상 총액 · 일행 전체", f"{result['total']:,}원")
    c.metric("부족한 금액" if result["remaining"] < 0 else "남는 예산", f"{abs(result['remaining']):,}원")
    st.caption(f"{people}명 · {duration} · 1인 평균 예상 {math.ceil(result['total'] / people):,}원. 이미 포함된 예비비와 남는 예산은 별도입니다.")
    st.dataframe(pd.DataFrame(result["breakdown"]), use_container_width=True, hide_index=True)
    if result.get("transport"):
        st.caption(f"출발지 확인: {result['origin_resolved']}")
        st.caption(result["transport"]["note"])
        st.caption(f"편도 이동 예상 {result['transport']['one_way_minutes']}분 · 현지 이동비 {result['transport']['local_cost']:,}원 별도 반영")
    if result["status"] == "over_budget":
        st.info(f"현재 입력 조건의 최소 코스 예상액은 {result['minimum_required']:,}원이에요. 예산을 초과하는 코스를 적정 예산인 것처럼 제시하지 않습니다.")
    if result["status"] != "ready":
        st.download_button("예산 계산표 CSV 다운로드", _budget_csv(result["breakdown"]), file_name="villageon_budget.csv", mime="text/csv", key="trip_budget_download")
        return
    st.subheader("시간순 여행 코스")
    st.caption("출발·귀가와 식사·휴식·현지 이동을 포함한 예상 일정입니다. 식당이 부족한 끼니는 ‘직접 선택’으로 표시합니다.")
    itinerary_df = pd.DataFrame(result["itinerary"])
    for day in range(1, days + 1):
        st.markdown(f"### {day}일차")
        day_df = itinerary_df[itinerary_df["일차"] == day].drop(columns=["일차"])
        st.dataframe(day_df, use_container_width=True, hide_index=True,
                     column_config={"카카오맵": st.column_config.LinkColumn("카카오맵", display_text="장소 확인")})
    map_places = pd.DataFrame(result["selected_places"])
    if not map_places.empty:
        st.subheader("코스에 포함된 실제 장소")
        st.caption("지도는 장소 위치를 표시합니다. 실제 이동 경로를 그린 길찾기 지도가 아닙니다.")
        trip_map_region = {**region, "lat": center[0], "lon": center[1]}
        st_folium(make_map(trip_map_region, map_places), height=430, use_container_width=True, returned_objects=[], key=f"trip_map_{fingerprint[:12]}")
    export_rows = []
    for row in result["itinerary"]:
        export_rows.append({"여행지": location_text(region), "인원": people, "여행일수": days, "전체예산(원)": budget,
                            "전체예상액(원)": result["total"], "남는예산(원)": result["remaining"], **row})
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("여행 코스 CSV 다운로드", _budget_csv(export_rows), file_name="villageon_itinerary.csv", mime="text/csv", key="trip_itinerary_download", use_container_width=True)
    with c2:
        st.download_button("예산 계산표 CSV 다운로드", _budget_csv(result["breakdown"]), file_name="villageon_budget.csv", mime="text/csv", key="trip_budget_download", use_container_width=True)


# ============================================================
# 화면 스타일: 배경과 입력 글자 색을 각각 지정합니다.
# ============================================================

def load_css():
    st.markdown(
        """
<style>
.stApp,
[data-testid="stAppViewContainer"] {
    background: #f7faf8;
    color: #172a3a;
    color-scheme: light;
}
[data-testid="stHeader"] { background: rgba(247, 250, 248, 0.96); }
[data-testid="stSidebar"] { background: #123c3a; color: #f7fbf8; }
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] [data-testid="stRadio"] label p,
[data-testid="stSidebar"] [data-testid="stCheckbox"] label p {
    color: #f7fbf8 !important;
}
[data-testid="stSidebar"] hr { border-color: #4d7470; }
[data-testid="stSidebar"] [data-testid="stAlert"] {
    background: #e9f3ee;
    color: #172a3a;
}
[data-testid="stSidebar"] [data-testid="stAlert"] p { color: #172a3a !important; }

/* 키 입력, 검색어, 숫자, 시간, 선택 상자에 동일한 읽기 쉬운 색을 사용합니다. */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input,
[data-testid="stTimeInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stSelectbox"] input,
[data-testid="stMultiSelect"] input,
[data-testid="stSelectbox"] [role="combobox"],
[data-testid="stMultiSelect"] [role="combobox"] {
    color: #172a3a !important;
    -webkit-text-fill-color: #172a3a !important;
    background-color: #ffffff !important;
    caret-color: #172a3a !important;
    opacity: 1 !important;
}
[data-testid="stTextInputRootElement"],
[data-testid="stNumberInputContainer"],
[data-testid="stTextArea"] [data-baseweb="textarea"],
[data-testid="stTimeInput"] [data-baseweb="select"] > div,
[data-testid="stDateInput"] [data-baseweb="input"],
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stMultiSelect"] [data-baseweb="select"] > div {
    color: #172a3a !important;
    background-color: #ffffff !important;
    border-color: #b9cdc4 !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] span,
[data-testid="stSelectbox"] [data-baseweb="select"] div,
[data-testid="stMultiSelect"] [data-baseweb="select"] span {
    color: #172a3a !important;
    -webkit-text-fill-color: #172a3a !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
    background: #dfefe6 !important;
    color: #123c3a !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder,
[data-testid="stSelectbox"] input::placeholder,
[data-testid="stMultiSelect"] input::placeholder {
    color: #667c73 !important;
    -webkit-text-fill-color: #667c73 !important;
    opacity: 1 !important;
}
[data-testid="stTextInput"] button,
[data-testid="stNumberInput"] button,
[data-testid="stSelectbox"] button,
[data-testid="stMultiSelect"] button,
[data-testid="stTimeInput"] button {
    color: #244d40 !important;
    background-color: #ffffff !important;
}
[data-testid="stTextInput"] svg,
[data-testid="stSelectbox"] svg,
[data-testid="stMultiSelect"] svg {
    color: #244d40 !important;
    fill: currentColor;
}

/* 선택 목록은 사이드바 바깥에 생성될 수도 있어 별도로 지정합니다. */
[role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"] {
    background-color: #ffffff !important;
    color: #172a3a !important;
}
[role="option"],
[role="option"] span,
[role="option"] div,
[role="option"] p,
[data-baseweb="menu"] li {
    color: #172a3a !important;
    -webkit-text-fill-color: #172a3a !important;
    background-color: #ffffff !important;
}
[role="option"][aria-selected="true"],
[role="option"][data-focused="true"],
[role="option"]:hover {
    background-color: #e0f0e7 !important;
}
[data-testid="stButton"] button,
[data-testid="stDownloadButton"] button {
    background: #ffffff;
    color: #123c3a;
    border: 1px solid #b9cdc4;
    border-radius: 8px;
}
[data-testid="stButton"] button[kind="primary"],
[data-testid="stDownloadButton"] button[kind="primary"] {
    background: #1f6b52;
    border-color: #1f6b52;
    color: #ffffff;
}
[data-testid="stButton"] button p,
[data-testid="stDownloadButton"] button p { color: inherit !important; }
[data-testid="stMain"] [data-testid="stWidgetLabel"] p,
[data-testid="stMain"] h1,
[data-testid="stMain"] h2,
[data-testid="stMain"] h3,
[data-testid="stMain"] [data-testid="stMetricValue"],
[data-testid="stMain"] [data-testid="stMetricLabel"] p {
    color: #172a3a;
}
.hero {
    background: #123c3a;
    color: #f7fbf8;
    padding: 42px;
    border-radius: 10px;
    margin-bottom: 24px;
}
.hero h1 { color: #ffffff !important; }
.hero p { color: #dcefe6 !important; }
.eyebrow {
    color: #bde7d5 !important;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
}
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #d9e4df;
    padding: 12px;
    border-radius: 8px;
}
</style>
""",
        unsafe_allow_html=True,
    )


def _region_selection_changed(level):
    """상위 지역을 바꾸면 하위 선택을 새 목록에 맞춰 초기화합니다."""
    st.session_state.pop("region_notice", None)
    if level == "sido":
        st.session_state.pop("region_sigungu", None)
        st.session_state.pop("region_eup", None)
    elif level == "sigungu":
        st.session_state.pop("region_eup", None)


def _recommend_region_callback(regions):
    current = (
        st.session_state.get("region_sido", ""),
        st.session_state.get("region_sigungu", ""),
        st.session_state.get("region_eup", ""),
    )
    selected = choose_random_region(regions, current)
    if selected is None:
        return
    # 버튼 콜백은 다음 화면의 위젯 생성 전에 실행됩니다.
    st.session_state["region_sido"] = selected["sido"]
    st.session_state["region_sigungu"] = selected["sigungu"]
    st.session_state["region_eup"] = selected["eupmyeondong"]
    st.session_state["view"] = "예산 여행 코스"
    st.session_state["region_notice"] = " ".join(
        selected[k] for k in ("sido", "sigungu", "eupmyeondong") if selected[k]
    )


def _ensure_region_option(key, options):
    if options and st.session_state.get(key) not in options:
        st.session_state[key] = options[0]


# ============================================================
# 앱 실행
# ============================================================

def main():
    load_css()
    for key, secret_name in (
        ("admin_api_key_input", "ADMIN_API_KEY"),
        ("kakao_api_key_input", "KAKAO_REST_API_KEY"),
    ):
        if key not in st.session_state:
            st.session_state[key] = load_secret_safely(secret_name)

    with st.sidebar:
        st.title("🌱 VillageON")
        st.caption("전국 지역을 발견하고, 내 예산으로 여행해요.")
        st.divider()
        st.subheader("API 설정")
        admin_api_key = st.text_input(
            "행정안전부 서비스키", type="password", key="admin_api_key_input",
            help="공공데이터포털 '행정안전부_행정표준코드_법정동코드' 일반 인증키(Decoding)",
        ).strip()
        kakao_api_key = st.text_input(
            "카카오 REST API 키", type="password", key="kakao_api_key_input",
            help="Kakao Developers 앱의 REST API 키. Local API 사용 설정과 권한도 확인해 주세요.",
        ).strip()
        st.caption("키는 비밀번호처럼 가려집니다. 오른쪽 눈 아이콘으로 입력 내용을 확인할 수 있어요.")
        st.divider()

    if not admin_api_key:
        st.title("전국 어디로 떠나 볼까요?")
        st.info("왼쪽에 행정안전부 서비스키를 입력하면 전국 지역 선택과 랜덤 추천을 사용할 수 있어요.")
        st.caption("실제 장소와 거리 기반 여행 코스를 만들 때는 카카오 REST API 키도 필요해요.")
        st.stop()

    try:
        with st.spinner("전국 법정동 목록을 모두 불러오는 중이에요. 처음에는 잠시 걸릴 수 있어요..."):
            admin_df = load_admin_regions(admin_api_key)
            regions = build_region_dataframe(admin_df)
    except AdminRegionLoadError as exc:
        st.error(str(exc))
        st.info("서비스 활용신청 승인, 일반 인증키(Decoding), 인터넷 연결을 확인한 뒤 다시 시도해 주세요.")
        if st.button("지역 목록 다시 불러오기", key="retry_regions"):
            load_admin_regions.clear()
            st.rerun()
        st.stop()
    if regions.empty:
        st.error("시·도와 읍·면·동까지 연결된 지역 목록을 받지 못했어요. 서비스키와 API 활용 승인을 확인해 주세요.")
        if st.button("지역 목록 다시 불러오기", key="retry_regions"):
            load_admin_regions.clear()
            st.rerun()
        st.stop()

    with st.sidebar:
        menu_options = ["지역 브리핑", "콘텐츠 탐색", "대시보드", "AI 추천", "숨은 명소", "예산 여행 코스"]
        _ensure_region_option("view", menu_options)
        view = st.radio("메뉴", menu_options, key="view")
        st.divider()
        st.subheader("전국 지역 선택")
        st.button(
            "🎲 지역 추천", key="recommend_region", use_container_width=True,
            on_click=_recommend_region_callback, args=(regions,),
            help="실제 행정구역 조합을 무작위로 고르고 예산 여행 코스로 이동해요.",
        )
        st.caption("시·도 → 시·군·구 → 읍·면·동을 한 번에 추천해요.")
        sido_options = sorted(regions["sido"].dropna().unique().tolist())
        _ensure_region_option("region_sido", sido_options)
        sido = st.selectbox(
            "시·도", sido_options, key="region_sido",
            on_change=_region_selection_changed, args=("sido",),
        )
        sigungu_options = sorted(regions.loc[regions["sido"] == sido, "sigungu"].unique().tolist())
        _ensure_region_option("region_sigungu", sigungu_options)
        sigungu = st.selectbox(
            "시·군·구", sigungu_options, key="region_sigungu",
            format_func=lambda value: value or "해당 없음 (세종특별자치시)",
            disabled=sigungu_options == [""],
            on_change=_region_selection_changed, args=("sigungu",),
        )
        eup_options = sorted(regions.loc[
            (regions["sido"] == sido) & (regions["sigungu"] == sigungu), "eupmyeondong"
        ].unique().tolist())
        _ensure_region_option("region_eup", eup_options)
        eup = st.selectbox(
            "읍·면·동", eup_options, key="region_eup",
            on_change=_region_selection_changed, args=("eup",),
        )
        st.caption("행정안전부 법정동 기준이에요. 일부 명칭은 주민센터의 행정동과 다를 수 있어요.")
        if st.session_state.get("region_notice"):
            st.success("추천 여행지: " + st.session_state["region_notice"])
        st.divider()
        interests = st.multiselect(
            "여행 관심사", ["자연", "미식", "역사", "휴식", "가족", "축제"],
            default=["자연", "미식"], key="travel_interests",
        )
        duration_options = {"당일치기": 1, "1박 2일": 2, "2박 3일": 3}
        duration = st.selectbox("여행 기간", list(duration_options), key="travel_duration")
        days = duration_options[duration]

    matches = regions[
        (regions["sido"] == sido)
        & (regions["sigungu"] == sigungu)
        & (regions["eupmyeondong"] == eup)
    ]
    if matches.empty:
        st.error("현재 선택과 연결된 지역을 찾지 못했어요. 지역 추천을 다시 눌러 주세요.")
        st.stop()
    selected = matches.iloc[0]
    resolved = resolve_region_location(sido, sigungu, eup, kakao_api_key)
    region = {
        "sido": sido, "sigungu": sigungu, "eup": eup,
        "region_code": selected["region_code"],
        "lat": resolved["lat"], "lon": resolved["lon"],
        "coordinates_verified": resolved["coordinates_verified"],
        "destination_address": " ".join(x for x in (sido, sigungu, eup) if x),
    }
    local_data = {}
    if kakao_api_key:
        with st.spinner("선택 지역 주변의 실제 장소를 찾고 있어요..."):
            local_data = collect_local_data(
                sido, sigungu, eup,
                region["lat"] if region["coordinates_verified"] else None,
                region["lon"] if region["coordinates_verified"] else None,
                kakao_api_key,
            )
        if not region["coordinates_verified"]:
            st.warning("선택한 읍·면·동의 좌표를 확인하지 못해 전체 지역명으로 장소를 검색했어요. 지도 중심은 시·도 기준입니다.")
        if not any(isinstance(frame, pd.DataFrame) and not frame.empty for frame in local_data.values()):
            st.info("실제 장소 검색 결과가 없어요. 카카오 키·Local API 권한을 확인하거나 다른 지역을 선택해 주세요.")
    else:
        st.warning("카카오 REST API 키를 입력하면 실제 장소와 출발지 거리를 검색할 수 있어요. 키 없이도 왕복 교통비를 직접 입력해 예산만 계산할 수 있어요.")
    st.caption("장소 검색은 선택 지역 주변을 포함합니다. 검색된 장소 수는 해당 지역 전체 시설 수나 공식 통계가 아닙니다.")
    analysis = calculate_region_analysis(local_data)

    if view == "지역 브리핑":
        render_home(region, analysis, local_data, interests, days)
    elif view == "콘텐츠 탐색":
        render_explore(region, kakao_api_key)
    elif view == "대시보드":
        render_dashboard(region, analysis)
    elif view == "AI 추천":
        render_ai(region, analysis, local_data, interests, days)
    elif view == "숨은 명소":
        render_hidden(region, kakao_api_key)
    elif view == "예산 여행 코스":
        render_budget_trip(region, local_data, interests, days, kakao_api_key)


if __name__ == "__main__":
    main()
