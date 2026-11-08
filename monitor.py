import base64
import json
import os
import time
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

URL = "https://www.jr-odekake.net/goyoyaku/campaign/sunriseseto_izumo/form.html"

YEAR = "2026"
MONTH = "11"
DAY = "20"
HOUR = "21"
MINUTE = "00"

TRAINS = {
    "サンライズ瀬戸": "seto",
    "サンライズ出雲": "izumo",
}

FACILITIES = [
    "シングルデラックス",
    "シングル",
    "ソロ",
    "サンライズツイン",
]

# 4設備すべてをメール通知の対象にする
NOTIFY_FACILITIES = {
    "シングルデラックス",
    "シングル",
    "ソロ",
    "サンライズツイン",
}

STATE_FILE = "sunrise-notification-state.json"
STATE_PATH = "sunrise-notification-state.json"


AVAILABLE_LABELS = {"空席あり", "空席残りわずか"}
UNAVAILABLE_LABEL = "残席なし"



def choose_option(page, selector, label):
    """プルダウンから表示名を指定して選択する。"""
    page.locator(selector).select_option(label=label)


def read_seat_statuses(section):
    """対象列車の設備欄にある空席状況を読み取る。"""
    images = section.locator(
        ".route-train-list__seat-info tbody td img[alt]"
    )

    statuses = []

    for i in range(images.count()):
        alt = images.nth(i).get_attribute("alt")

        if alt:
            statuses.append(alt.strip())

    return statuses


def classify_result(page, train_name, facility):
    """検索結果をAVAILABLE / UNAVAILABLE / UNKNOWNに分類する。"""
    body = page.locator("body").inner_text(timeout=15000)

    if "混雑中です" in body or "20100946" in body:
        return "UNKNOWN", "予約サイトの混雑エラー"

    if "検索条件" not in body:
        return "UNKNOWN", "検索結果画面を確認できません"

    sections = page.locator(".route-train-list__section-body")
    target_sections = []
    train_keyword = f"特急{train_name}"

    for i in range(sections.count()):
        section = sections.nth(i)

        name_locator = section.locator(
            ".route-train-list__train-name p"
        )

        if name_locator.count() == 0:
            continue

        displayed_name = name_locator.first.inner_text().strip()

        if train_keyword not in displayed_name:
            continue

        if facility == "シングルデラックス":
            target_sections.append(section)

        elif facility == "サンライズツイン":
            if "（サツイン）" in displayed_name:
                target_sections.append(section)

        elif f"（{facility}）" in displayed_name:
            target_sections.append(section)

    if not target_sections:
        return (
            "UNKNOWN",
            f"対象列車・設備の結果を特定できません"
            f"（{train_name} / {facility}）",
        )

    statuses = []

    for section in target_sections:
        statuses.extend(read_seat_statuses(section))

    if not statuses:
        return "UNKNOWN", "対象設備の空席表示が見つかりません"

    if any(status in AVAILABLE_LABELS for status in statuses):
        return (
            "AVAILABLE",
            f"空席あり・残りわずかの設備あり（表示数: {len(statuses)}）",
        )

    if all(status == UNAVAILABLE_LABEL for status in statuses):

        if facility == "シングルデラックス" and len(statuses) != 5:
            return (
                "UNKNOWN",
                "シングルデラックス検索の設備表示が"
                f"5種類ではありません（表示数: {len(statuses)}）",
            )

        if facility == "サンライズツイン" and len(statuses) != 2:
            return (
                "UNKNOWN",
                "サンライズツインの設備表示が"
                f"2種類ではありません（表示数: {len(statuses)}）",
            )

        return (
            "UNAVAILABLE",
            f"対象設備はすべて残席なし（表示数: {len(statuses)}）",
        )

    return (
        "UNKNOWN",
        f"空席表示を確定できません"
        f"（表示数: {len(statuses)}、内容: {statuses}）",
    )


def search_one(page, train_name, train_value, facility, retry=True):
    """列車1種類・設備1種類について検索する。"""
    result = {
        "train": train_name,
        "facility": facility,
        "status": "UNKNOWN",
        "detail": "",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    label = f"{train_name}_{facility}"
    print(f"検索開始: {train_name} / {facility}")

    try:
        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.locator("#member-no").click()
        page.locator("#jsSelectYear").wait_for(timeout=30000)

        choose_option(page, "#jsSelectYear", f"{YEAR}年")
        choose_option(page, "#jsSelectMonth", f"{int(MONTH)}月")
        choose_option(page, "#jsSelectDay", f"{int(DAY)}日")
        choose_option(page, "#jsSelectHour", HOUR)
        choose_option(page, "#jsSelectMinute", MINUTE)

        # JRの検索処理は、画面上の日付プルダウンではなく
        # hidden input #inputDate の値を送信するため、明示的に設定する。
        target_date = f"{YEAR}{MONTH}{DAY}"
        page.locator("#inputDate").evaluate(
            """(el, value) => {
                el.value = value;
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
            }""",
            target_date,
        )

        # 送信直前に、意図した日付がセットされていることを必ず確認する。
        actual_date = page.locator("#inputDate").input_value()
        print(f"送信直前の inputDate: {actual_date}")
        if actual_date != target_date:
            raise RuntimeError(
                f"日付の設定に失敗しました（期待値: {target_date} / 実際: {actual_date}）"
            )

        page.locator("#jsSelectTrainType").select_option(train_value)
        choose_option(page, "#inputDepartStName", "東京")
        choose_option(page, "#inputArriveStName", "岡山")

        page.locator(
            f'input[name="facilitySelect"][value="{facility}"]'
        ).check(timeout=15000)

        submit = page.locator("#submitButton")
        submit.wait_for(state="visible", timeout=15000)

        deadline = time.time() + 15

        while not submit.is_enabled() and time.time() < deadline:
            page.wait_for_timeout(500)

        if not submit.is_enabled():
            result["detail"] = "検索ボタンが有効になりませんでした"
            return result

        submit.click()
        page.wait_for_timeout(2500)

        status, detail = classify_result(
            page,
            train_name,
            facility,
        )

        result["status"] = status
        result["detail"] = detail

        # 混雑エラーの場合だけ、20秒待って1回再検索する
        if retry and status == "UNKNOWN" and detail == "予約サイトの混雑エラー":
            print(f"混雑エラーのため20秒待って再検索: {label}")
            page.wait_for_timeout(20000)

            return search_one(
                page,
                train_name,
                train_value,
                facility,
                retry=False,
            )

        return result

    except PlaywrightTimeoutError:
        result["detail"] = "画面の読み込みまたは操作がタイムアウトしました"
        return result

    except Exception as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result


def github_api_request(method, url, payload=None):
    """GitHub APIを呼び出す。認証情報はログに出さない。"""
    token = os.environ.get("GITHUB_TOKEN")

    if not token:
        raise RuntimeError("GITHUB_TOKENが設定されていません")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "sunrise-monitor",
    }

    data = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_data = response.read()

            if not response_data:
                return {}

            return json.loads(response_data.decode("utf-8"))

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None

        raise RuntimeError(
            f"GitHub APIエラー（HTTP {exc.code}）"
        ) from exc


def load_notification_state():
    """前回通知した空席情報をGitHubから読み込む。"""
    repository = os.environ.get("GITHUB_REPOSITORY")

    if not repository:
        raise RuntimeError("GITHUB_REPOSITORYが設定されていません")

    url = (
        f"https://api.github.com/repos/{repository}"
        f"/contents/{STATE_PATH}"
    )

    result = github_api_request("GET", url)

    if result is None:
        return [], None

    content = result.get("content", "")
    decoded = base64.b64decode(content).decode("utf-8")
    state = json.loads(decoded)

    return state.get("notified", []), result.get("sha")


def save_notification_state(notified, sha):
    """通知済み情報をGitHubリポジトリに保存する。"""
    repository = os.environ.get("GITHUB_REPOSITORY")

    if not repository:
        raise RuntimeError("GITHUB_REPOSITORYが設定されていません")

    url = (
        f"https://api.github.com/repos/{repository}"
        f"/contents/{STATE_PATH}"
    )

    state = {
        "notified": notified,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    content = base64.b64encode(
        json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": "Update Sunrise notification state",
        "content": content,
    }

    if sha:
        payload["sha"] = sha

    github_api_request("PUT", url, payload)


def send_gmail(available):
    """空席情報をGmailで送信する。"""
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not address or not app_password:
        raise RuntimeError("GmailのSecretsが設定されていません")

    checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    lines = [
        "サンライズの個室に空席が見つかりました。",
        "",
        "対象：2026年11月20日 東京発 → 岡山着",
        f"確認時刻：{checked_at}",
        "",
    ]

    for item in available:
        lines.append(
            f"{item['train']} / {item['facility']}"
        )
        lines.append(f"状況：{item['status']}")
        lines.append(f"詳細：{item['detail']}")
        lines.append("")

    lines.extend([
        "空席状況は確認時点の表示です。",
        "予約が完了したわけではありません。",
    ])

    message = EmailMessage()
    message["Subject"] = "【サンライズ空席通知】個室に空席の可能性"
    message["From"] = address
    message["To"] = address
    message.set_content("\n".join(lines))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(address, app_password)
        smtp.send_message(message)

    print("Gmail通知を送信しました。")


def notify_if_needed(results):
    """対象設備の空席を通知し、重複通知を抑制する。"""
    available = [
        item for item in results
        if item["facility"] in NOTIFY_FACILITIES
        and item["status"] == "AVAILABLE"
    ]

    # 同じ設備が空席のままなら、同じ内容を再通知しない
    current = sorted(
        f"{item['train']}|{item['facility']}"
        for item in available
    )

    try:
        previous, sha = load_notification_state()

        if not current:
            if previous:
                save_notification_state([], sha)
                print("空席がなくなったため、通知状態をリセットしました。")
            else:
                print("通知対象の空席はありません。メールは送信しません。")
            return

        if current == previous:
            print("前回と同じ空席情報のため、重複通知を省略します。")
            return

        send_gmail(available)
        save_notification_state(current, sha)

    except Exception as exc:
        print(
            "通知処理でエラーが発生しました。"
            "空席確認レポートは引き続き保存します。"
        )
        print(f"通知エラー: {type(exc).__name__}: {exc}")


def main():
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1440, "height": 1200},
            locale="ja-JP",
        )

        try:
            cases = [
                (train_name, train_value, facility)
                for train_name, train_value in TRAINS.items()
                for facility in FACILITIES
            ]

            for index, (train_name, train_value, facility) in enumerate(cases):
                result = search_one(
                    page,
                    train_name,
                    train_value,
                    facility,
                )

                results.append(result)

                print(
                    f"{train_name} / {facility}: "
                    f"{result['status']} — {result['detail']}"
                )

                if index < len(cases) - 1:
                    print("次の検索まで20秒待機します。")
                    page.wait_for_timeout(20000)

        finally:
            browser.close()

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "target": {
            "date": f"{YEAR}-{MONTH}-{DAY}",
            "time": f"{HOUR}:{MINUTE}",
            "departure": "東京",
            "arrival": "岡山",
        },
        "results": results,
        "status_guide": {
            "AVAILABLE": "対象設備のどれかに空席あり・残りわずか",
            "UNAVAILABLE": "対象設備がすべて残席なし",
            "UNKNOWN": "判定不能。空席なしとは限らない",
        },
        "notice": "空席状況は確認時点の表示です。",
    }


    # レポート保存後に通知する
    notify_if_needed(results)

    print("\n確認終了。結果は実行ログに要約のみ表示します。")


if __name__ == "__main__":
    main()
