import os
from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://www.jr-odekake.net/goyoyaku/campaign/sunriseseto_izumo/form.html"

# まずは1パターンだけ調べる（予約監視本体には影響しない）
YEAR, MONTH, DAY = "2026", "11", "20"
HOUR, MINUTE = "21", "00"
TRAIN_NAME = "サンライズ瀬戸"
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
            page.locator("#member-no").click()
            page.locator("#jsSelectYear").wait_for(timeout=30000)

            choose_option(page, "#jsSelectYear", f"{YEAR}年")
            choose_option(page, "#jsSelectMonth", f"{int(MONTH)}月")
            choose_option(page, "#jsSelectDay", f"{int(DAY)}日")
            choose_option(page, "#jsSelectHour", HOUR)
            choose_option(page, "#jsSelectMinute", MINUTE)
            page.locator("#jsSelectTrainType").select_option(TRAIN_VALUE)
            choose_option(page, "#inputDepartStName", "東京")
            choose_option(page, "#inputArriveStName", "岡山")
            page.locator(
                f'input[name="facilitySelect"][value="{FACILITY}"]'
            ).check(timeout=15000)

            submit = page.locator("#submitButton")
            submit.wait_for(state="visible", timeout=15000)
            submit.wait_for(state="attached", timeout=15000)

            deadline = __import__("time").time() + 15
            while not submit.is_enabled() and __import__("time").time() < deadline:
                page.wait_for_timeout(500)
            if not submit.is_enabled():
                raise RuntimeError("検索ボタンが有効になりませんでした")

            print("検索を実行します")
            submit.click()

            # 本体の2.5秒待機と比較できるよう、診断では最大30秒待ってから保存
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
