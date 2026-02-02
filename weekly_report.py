import os
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]
STRAVA_CLUB_ID = os.environ["STRAVA_CLUB_ID"]

BAND_ACCESS_TOKEN = os.environ["BAND_ACCESS_TOKEN"]
BAND_KEY = os.environ["BAND_KEY"]

TOP_N = int(os.environ.get("TOP_N", "20"))


def kst_now():
    return datetime.now(tz=KST)


def last_week_range_kst(now_kst: datetime):
    """
    지난주 월요일 00:00:00 ~ 이번주 월요일 00:00:00 (KST)
    """
    # 이번주 월요일 00:00
    this_monday = (now_kst - timedelta(days=now_kst.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = this_monday - timedelta(days=7)
    end = this_monday
    return start, end


def refresh_strava_access_token():
    url = "https://www.strava.com/oauth/token"
    payload = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": STRAVA_REFRESH_TOKEN,
    }
    r = requests.post(url, data=payload, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_club_activities(access_token: str, per_page=200, max_pages=10):
    """
    클럽 활동을 페이지네이션으로 가져옴.
    Strava API는 '클럽 주간 순위표'를 직접 주지 않아서,
    활동 리스트를 받아서 우리가 집계해야 함.
    """
    url = f"https://www.strava.com/api/v3/clubs/{STRAVA_CLUB_ID}/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []

    for page in range(1, max_pages + 1):
        params = {"page": page, "per_page": per_page}
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        activities.extend(batch)

    return activities


def to_kst(dt_str: str) -> datetime:
    # Strava는 보통 ISO 문자열(UTC)을 줌. 예: "2026-02-01T03:12:34Z"
    if dt_str.endswith("Z"):
        dt_str = dt_str.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(dt_str)
    return dt_utc.astimezone(KST)


def build_leaderboard(activities, start_kst: datetime, end_kst: datetime):
    """
    사람별 거리/고도 합산 후 정렬
    distance: meters → km
    total_elevation_gain: meters
    """
    by_athlete = {}

    for a in activities:
        # 활동 시작 시각
        dt = to_kst(a["start_date"])
        if not (start_kst <= dt < end_kst):
            continue

        athlete = a.get("athlete", {})
        athlete_id = athlete.get("id")
        firstname = athlete.get("firstname", "")
        lastname = athlete.get("lastname", "")
        name = (firstname + " " + lastname).strip() or f"athlete_{athlete_id}"

        dist_m = float(a.get("distance", 0.0))
        elev_m = float(a.get("total_elevation_gain", 0.0))

        if athlete_id not in by_athlete:
            by_athlete[athlete_id] = {"name": name, "dist_m": 0.0, "elev_m": 0.0, "rides": 0}

        by_athlete[athlete_id]["dist_m"] += dist_m
        by_athlete[athlete_id]["elev_m"] += elev_m
        by_athlete[athlete_id]["rides"] += 1

    rows = []
    for _, v in by_athlete.items():
        rows.append({
            "name": v["name"],
            "km": v["dist_m"] / 1000.0,
            "elev": v["elev_m"],
            "rides": v["rides"],
        })

    # 정렬: 거리 내림차순, 고도 내림차순
    rows.sort(key=lambda x: (x["km"], x["elev"]), reverse=True)
    return rows


def format_post_text(start_kst: datetime, end_kst: datetime, leaderboard):
    # 기간 표기: 지난주 월~일
    # end는 이번주 월요일 00:00이므로, end-1day는 지난주 일요일
    start_s = start_kst.strftime("%m/%d(월)")
    end_s = (end_kst - timedelta(days=1)).strftime("%m/%d(일)")

    lines = []
    lines.append(f"🏁 지난주 클럽 랭킹 ({start_s} ~ {end_s})")
    lines.append("")
    lines.append("📌 기준: 거리(km) / 획득고도(m) / 횟수")
    lines.append("")

    if not leaderboard:
        lines.append("지난주 기록된 활동이 없어요 🥲")
        return "\n".join(lines)

    lines.append(f"🏆 TOP {min(TOP_N, len(leaderboard))}")
    for i, row in enumerate(leaderboard[:TOP_N], start=1):
        lines.append(
            f"{i:>2}. {row['name']}  |  {row['km']:.1f} km  |  {row['elev']:.0f} m  |  {row['rides']}회"
        )

    lines.append("")
    total_km = sum(r["km"] for r in leaderboard)
    total_elev = sum(r["elev"] for r in leaderboard)
    total_rides = sum(r["rides"] for r in leaderboard)
    lines.append(f"📊 전체 합계: {total_km:.1f} km / {total_elev:.0f} m / {total_rides}회 (참여 {len(leaderboard)}명)")

    return "\n".join(lines)


def post_to_band(text: str):
    url = "https://openapi.band.us/v2.2/band/post/create"
    data = {
        "access_token": BAND_ACCESS_TOKEN,
        "band_key": BAND_KEY,
        "content": text,
    }
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("result_code") != 1:
        raise RuntimeError(f"BAND API error: {j}")
    return j


def main():
    now = kst_now()
    start_kst, end_kst = last_week_range_kst(now)

    access_token = refresh_strava_access_token()
    activities = fetch_club_activities(access_token)

    leaderboard = build_leaderboard(activities, start_kst, end_kst)
    text = format_post_text(start_kst, end_kst, leaderboard)

    post_to_band(text)
    print("✅ Posted to BAND successfully.")
    print(text)


if __name__ == "__main__":
    main()
