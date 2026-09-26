from playwright.sync_api import sync_playwright

url = "https://contour-software.com/contact-us/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)

    page = browser.new_page()

    page.goto(
        url,
        wait_until="networkidle",
        timeout=60000
    )

    print("TITLE:")
    print(page.title())

    html = page.content()

    print("\nHTML LENGTH:")
    print(len(html))

    print("\nBODY TEXT LENGTH:")
    text = page.locator("body").inner_text()
    print(len(text))

    print("\nHTML FIRST 1000 CHARACTERS:")
    print(html[:1000])

    browser.close()