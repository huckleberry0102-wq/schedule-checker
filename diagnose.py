import os
from pathlib import Path
from datetime import datetime, timezone
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://www.jr-odekake.net/goyoyaku/campaign/sunriseseto_izumo/form.html"

# 診断対象：2026年11月20日、東京→岡山、サンライズ瀬戸、シングルデラックス
YEAR, MONTH, DAY = "2026", "11", "20"
HOUR, MINUTE = "21", "00"
TRAIN_VALUE = "seto"
FACILITY = "シングルデラックス"


def choose_option(page, selector, label):
    page.locator(selector).select_option(label=label)


def main():
    out = Path("diagnostics")
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    screenshot = out / f"result-{stamp}.png"
    html_file = out / f"result-{stamp}.html"
    text_file = out / f"result-{stamp}.txt"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1200},
            locale="ja-JP",
        )
        try:
            print("JR公式サイトを開きます")
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)

            # 「会員ログインせず予約」を選択
            page.locator("#member-no").click()
            page.locator("#jsSelectYear").wait_for(timeout=30000)

            # 画面上の日付・時刻を指定
            choose_option(page, "#jsSelectYear", f"{YEAR}年")
            choose_option(page, "#jsSelectMonth", f"{int(MONTH)}月")
            choose_option(page, "#jsSelectDay", f"{int(DAY)}日")
            choose_option(page, "#jsSelectHour", HOUR)
            choose_option(page, "#jsSelectMinute", MINUTE)

            # JRページは画面の日付選択とは別に hidden の inputDate を送信する。
            # 選択操作だけでは hidden 値が空のままになる場合があるため、送信値を明示する。
            page.locator("#inputDate").evaluate(
                "(el, value) => { el.value = value; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
                f"{YEAR}{MONTH}{DAY}",
            )

            # 列車・区間・設備
            page.locator("#jsSelectTrainType").select_option(TRAIN_VALUE)
            choose_option(page, "#inputDepartStName", "東京")
            choose_option(page, "#inputArriveStName", "岡山")
            page.locator(
                f'input[name="facilitySelect"][value="{FACILITY}"]'
            ).check(timeout=15000)

            # 送信前に hidden 日付値を必ず確認
            hidden_date = page.locator("#inputDate").input_value()
            print("送信直前の inputDate:", hidden_date)
            if hidden_date != f"{YEAR}{MONTH}{DAY}":
                raise RuntimeError(
                    f"日付の送信値が正しくありません: inputDate={hidden_date!r}"
                )

            submit = page.locator("#submitButton")
            submit.wait_for(state="visible", timeout=15000)
            deadline = time.time() + 15
            while not submit.is_enabled() and time.time() < deadline:
                page.wait_for_timeout(500)
            if not submit.is_enabled():
                raise RuntimeError("検索ボタンが有効になりませんでした")

            print("検索を実行します")
            submit.click()

            # 結果画面の表示を待つ
            try:
                page.locator(".route-train-list__section-body").first.wait_for(timeout=30000)
            except PlaywrightTimeoutError:
                print("結果一覧のセレクターは30秒以内に見つかりませんでした")
            page.wait_for_timeout(5000)

            body_text = page.locator("body").inner_text(timeout=15000)
            page.screenshot(path=str(screenshot), full_page=True)
            html_file.write_text(page.content(), encoding="utf-8")
            text_file.write_text(
                "URL: " + page.url + "\n"
                "TITLE: " + page.title() + "\n\n"
                + body_text,
                encoding="utf-8",
            )

            print("診断ファイルを保存しました")
            print("TITLE:", page.title())
            print("URL:", page.url)
            print("--- 画面に表示された本文 ---")
            print(body_text[:12000])
            print("--- 空席画像 alt 属性 ---")
            alts = page.locator(".route-train-list__seat-info tbody td img[alt]")
            for i in range(min(alts.count(), 100)):
                print(i + 1, repr(alts.nth(i).get_attribute("alt")))
            print("SCREENSHOT:", screenshot)
            print("HTML:", html_file)
            print("TEXT:", text_file)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
